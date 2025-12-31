"""Modal worker for SoundCloud/YouTube to MP3 conversion."""
import io
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any

import modal


APP_NAME = "converter"
CPU, MEMORY, TIMEOUT = 1.0, 512, 60
KEEP_WARM = 1
MAX_DURATION = 600
AUDIO_QUALITY = "320"
AUDIO_FORMAT = "mp3"


YTDLP_ARGS = [
    "--no-warnings",
    "--no-playlist",
    "--extractor-args", "youtube:player_client=android",
    "--socket-timeout", "30",
]


image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "ca-certificates")
    .pip_install("yt-dlp==2024.12.6")
)


app = modal.App(name=APP_NAME, image=image)


@dataclass(frozen=True, slots=True)
class DownloadRequest:
    id: str
    url: str


@dataclass(frozen=True, slots=True)
class DownloadResult:
    id: str
    status: str  # "success" | "error"
    b64: str | None = None
    error: str | None = None
    metadata: Any = None


def _write_stream(source, dest, chunk: int = 65536) -> int:
    """Stream bytes from source to dest, returning total bytes written."""
    total = 0
    while True:
        data = source.read(chunk)
        if not data:
            break
        dest.write(data)
        total += len(data)
    return total


def _get_duration(url: str) -> int | None:
    """Extract duration in seconds from yt-dlp metadata."""
    proc = subprocess.Popen(
        ["yt-dlp", *YTDLP_ARGS, "--dump-json", "--skip-download", url],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, _ = proc.communicate(timeout=30)
    if proc.returncode != 0:
        raise RuntimeError("Failed to fetch metadata")

    obj = json.loads(stdout)
    return obj.get("duration")


def _download_mp3(url: str, out: io.BytesIO):
    """Download and convert to MP3, returning (bytes, metadata)."""
    print(f"[DEBUG] Starting download for: {url}")

    # First get metadata and available formats
    proc = subprocess.Popen(
        ["yt-dlp", *YTDLP_ARGS, "--dump-json", "--skip-download", url],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = proc.communicate(timeout=30)
    print(f"[DEBUG] Metadata returncode: {proc.returncode}")
    if stderr:
        print(f"[DEBUG] Metadata stderr: {stderr.decode()[:500]}")
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to fetch metadata: {stderr.decode()}")
    metadata = json.loads(stdout)
    print(f"[DEBUG] Got metadata: {metadata.get('title')}")
    print(f"[DEBUG] Available formats: {[f.get('format_id') for f in metadata.get('formats', [])[:10]]}")

    duration = metadata.get("duration")
    if duration and duration > MAX_DURATION:
        raise ValueError(f"Duration {duration}s exceeds limit")

    # Try to prefer direct MP3 over HLS formats
    # Format preference: http_mp3 > hls_mp3 > bestaudio
    cmd = [
        "yt-dlp", *YTDLP_ARGS,
        "-f", "http_mp3_1_0/hls_mp3_1_0/bestaudio",
        "-o", "-",
        url,
    ]
    print(f"[DEBUG] Running: {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    size = _write_stream(proc.stdout, out)
    _, stderr = proc.communicate()

    print(f"[DEBUG] Download returncode: {proc.returncode}, size: {size} bytes")
    if stderr:
        print(f"[DEBUG] stderr: {stderr.decode()[:1000]}")

    if proc.returncode != 0:
        raise RuntimeError(f"Download failed: {stderr.decode()}")

    if size < 1024:
        raise ValueError(f"Download too small: {size} bytes")

    # Check header
    out.seek(0)
    header = out.read(3)
    print(f"[DEBUG] Output header: {header!r}")

    if not (header == b"ID3" or header[:1] == b"\xff"):
        raise ValueError(f"Invalid MP3 header: {header!r}")

    return size, metadata


@app.function(cpu=CPU, memory=MEMORY, timeout=TIMEOUT, min_containers=KEEP_WARM)
def _download_single(req: DownloadRequest) -> DownloadResult:
    """Download and convert single URL to MP3, return result with base64 data."""
    try:
        print(f"[DEBUG] _download_single called with id={req.id}, url={req.url[:50]}...")
        buf = io.BytesIO()
        size, metadata = _download_mp3(req.url, buf)

        buf.seek(0)
        header = buf.read(3)
        print(f"[DEBUG] First 3 bytes: {header!r}")

        import base64
        b64_data = base64.b64encode(buf.getvalue()).decode()

        return DownloadResult(
            id=req.id,
            status="success",
            b64=b64_data,
            metadata={
                "title": metadata.get("title"),
                "uploader": metadata.get("uploader"),
                "duration": metadata.get("duration"),
            },
        )

    except Exception as e:
        print(f"[DEBUG] Exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return DownloadResult(id=req.id, status="error", error=str(e)[:200])


@app.function()
def download(requests: list[dict]) -> list[dict]:
    """Download multiple URLs in parallel, return all results (success + error)."""
    if not requests:
        return []

    typed = [
        DownloadRequest(id=str(r.get("id", i)), url=str(r["url"]))
        for i, r in enumerate(requests)
    ]

    results = _download_single.map(typed)

    return [
        {
            "id": r.id,
            "status": r.status,
            "b64": r.b64,
            "error": r.error,
            "metadata": r.metadata,
        }
        for r in results
    ]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

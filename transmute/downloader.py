"""Local yt-dlp download pipeline."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_WORKERS = 4

URL_RE = re.compile(r"https?://(?:(?!https?://)\S)+")


@dataclass
class Job:
    url: str
    status: str = "queued"  # queued | downloading | converting | done | error
    title: str | None = None
    uploader: str | None = None
    duration: int | None = None
    description: str | None = None
    tags: list[str] | None = None
    path: Path | None = None
    error: str | None = None


@dataclass
class Settings:
    out_dir: Path = field(default_factory=lambda: Path.home() / "Downloads")
    quality: str = "320"


def extract_urls(text: str) -> list[str]:
    """Pull URLs out of free text, including ones pasted back-to-back."""
    return URL_RE.findall(text)


def download_job(job: Job, settings: Settings, on_progress=None) -> Job:
    """Download one URL to MP3. on_progress(job, fraction|None) fires on updates."""
    import yt_dlp

    settings.out_dir.mkdir(parents=True, exist_ok=True)

    def hook(d: dict) -> None:
        info = d.get("info_dict") or {}
        if info.get("title"):
            job.title = info["title"]
        if d["status"] == "downloading":
            job.status = "downloading"
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if on_progress and total:
                on_progress(job, d.get("downloaded_bytes", 0) / total)
        elif d["status"] == "finished":
            job.status = "converting"
            if on_progress:
                on_progress(job, None)

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(settings.out_dir / "%(uploader)s - %(title)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "writethumbnail": True,
        "progress_hooks": [hook],
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": settings.quality,
            },
            {"key": "FFmpegMetadata"},
            {"key": "EmbedThumbnail"},
        ],
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(job.url, download=True)
        job.title = info.get("title") or job.title
        job.uploader = info.get("uploader")
        job.duration = info.get("duration")
        job.description = info.get("description")
        job.tags = info.get("tags")
        downloads = info.get("requested_downloads") or []
        if downloads and downloads[0].get("filepath"):
            job.path = Path(downloads[0]["filepath"])
        job.status = "done"
    except Exception as e:
        job.status = "error"
        job.error = str(e).split("\n")[0][:200]

    return job

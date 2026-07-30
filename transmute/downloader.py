"""Local yt-dlp download pipeline."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import Settings

URL_RE = re.compile(r"https?://(?:(?!https?://)\S)+")
JobStatus = Literal["queued", "downloading", "converting", "done", "error"]
ProgressCallback = Callable[["Job", float | None], None]


@dataclass
class Job:
    url: str
    status: JobStatus = "queued"
    title: str | None = None
    uploader: str | None = None
    duration: int | None = None
    description: str | None = None
    tags: list[str] | None = None
    path: Path | None = None
    error: str | None = None


def extract_urls(text: str) -> list[str]:
    """Pull URLs out of free text, including ones pasted back-to-back."""
    return URL_RE.findall(text)


def download_job(
    job: Job,
    settings: Settings,
    on_progress: ProgressCallback | None = None,
) -> Job:
    """Download one URL to MP3 and normalize third-party failures onto `job`."""
    import yt_dlp

    def hook(d: dict) -> None:
        info = d.get("info_dict") or {}
        if info.get("title"):
            job.title = info["title"]
        status = d.get("status")
        if status == "downloading":
            job.status = "downloading"
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if on_progress and total:
                on_progress(job, d.get("downloaded_bytes", 0) / total)
        elif status == "finished":
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
        settings.out_dir.mkdir(parents=True, exist_ok=True)
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
    except Exception as e:  # noqa: BLE001
        job.status = "error"
        job.error = _error_line(e)

    return job


def _error_line(error: Exception) -> str:
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    return (lines[0] if lines else error.__class__.__name__)[:200]

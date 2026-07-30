"""Local yt-dlp download pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .config import Settings

URL_RE = re.compile(r"https?://(?:(?!https?://)\S)+")

# Hosts we can actually download from. Subdomains (www., m., music., on., …)
# are matched too, so e.g. music.youtube.com and on.soundcloud.com are allowed.
#
# TO ADD A NEW MEDIA SOURCE: append its bare registrable domain here (e.g.
# "bandcamp.com", "vimeo.com"). That is the single choke point — is_supported_url
# and the input gate in App._accept both read from this tuple, so no other code
# needs to change to widen what the app accepts. Only add a host once yt-dlp can
# actually extract audio from it, otherwise links pass the gate and fail later at
# download time.
SUPPORTED_HOSTS = ("youtube.com", "youtu.be", "soundcloud.com")


def is_supported_url(url: str) -> bool:
    """True only for links to a supported media source (see SUPPORTED_HOSTS).

    We gate input up front rather than letting anything ``http(s)://`` through
    for two reasons: the download pipeline is built around yt-dlp audio
    extraction, which only works for known media hosts, and rejecting an
    unsupported paste immediately gives the user a clear message instead of a
    cryptic failure minutes later. Matching is anchored to each host's registrable
    domain (``host == h`` or ``host.endswith("." + h)``) so subdomains like
    ``music.youtube.com`` are accepted while look-alikes like
    ``youtube.com.evil.com`` are not.

    To support a new format/source, add its host to SUPPORTED_HOSTS above; this
    function and the App._accept input gate pick it up automatically.
    """
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in SUPPORTED_HOSTS)


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
    except Exception as e:  # noqa: BLE001
        job.status = "error"
        job.error = str(e).split("\n")[0][:200]

    return job

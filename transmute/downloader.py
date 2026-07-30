"""Local yt-dlp download pipeline."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from .config import Settings

URL_RE = re.compile(r"https?://(?:(?!https?://)\S)+")
JobStatus = Literal["queued", "downloading", "converting", "done", "error"]
ProgressCallback = Callable[["Job", float | None], None]
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# (substring of the lowercased yt-dlp message, short human message, retryable)
# Non-retryable means retrying the same URL cannot succeed without the user
# changing something first.
_ERROR_PATTERNS: list[tuple[str, str, bool]] = [
    ("unsupported url", "", False),  # message built from the domain, see below
    ("is not a valid url", "not a valid link", False),
    ("private video", "video is private", False),
    ("video unavailable", "video unavailable", False),
    ("has been removed", "video was removed", False),
    ("no longer available", "video is no longer available", False),
    ("sign in to confirm", "requires login — needs browser cookies", False),
    ("login required", "requires login — needs browser cookies", False),
    ("not available in your country", "not available in your region", False),
    ("ffprobe and ffmpeg not found", "ffmpeg missing — brew install ffmpeg", False),
    ("timed out", "network error — timed out", True),
    ("temporary failure in name resolution", "network error — DNS failed", True),
    ("getaddrinfo failed", "network error — DNS failed", True),
    ("connection", "network error — connection failed", True),
    ("http error 5", "server error — try again later", True),
    ("unable to download", "network error — download failed", True),
]

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
    status: JobStatus = "queued"
    title: str | None = None
    uploader: str | None = None
    duration: int | None = None
    description: str | None = None
    tags: list[str] | None = None
    path: Path | None = None
    error: str | None = None  # short human-readable summary
    error_detail: str | None = None  # full untruncated message from yt-dlp
    retryable: bool = True
    history_id: str = field(default_factory=lambda: uuid4().hex, compare=False)


class _SilentLogger:
    """Swallow yt-dlp's log output; even with quiet=True it prints errors to
    stderr, which corrupts the full-screen TUI. Failures reach us as raised
    exceptions instead."""

    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


def classify_error(e: Exception, url: str) -> tuple[str, str, bool]:
    """Turn a raw yt-dlp exception into (summary, detail, retryable).

    yt-dlp embeds ANSI color codes in its exception messages when it detects
    a tty, so strip those before anything else.
    """
    detail = ANSI_RE.sub("", str(e)).strip()
    low = detail.lower()
    for needle, summary, retryable in _ERROR_PATTERNS:
        if needle in low:
            if needle == "unsupported url":
                domain = urlparse(url).netloc or url
                summary = f"{domain} isn't a supported site"
            return summary, detail, retryable
    first = detail.split("\n")[0].removeprefix("ERROR: ").strip()[:120]
    return first or "unknown error", detail, True


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
        "color": "never",  # keep ANSI codes out of exception messages
        "logger": _SilentLogger(),
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
        job.error, job.error_detail, job.retryable = classify_error(e, job.url)

    return job

"""Bounded source retrieval for the browser prototype; never encodes or tags audio."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from transmute.downloader import is_supported_url

MAX_BYTES = 50 * 1024 * 1024
MAX_SECONDS = 20 * 60


def validate_url(value: object) -> str:
    """Accept one public source URL, with no credentials or custom port."""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Paste one YouTube or SoundCloud track link.")
    url = value.strip()
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme == "https"
            and not parts.username
            and not parts.password
            and parts.port in (None, 443)
            and not any(char.isspace() for char in url)
            and is_supported_url(url)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Paste one HTTPS YouTube or SoundCloud track link.")
    return url


def check_track(info: dict) -> None:
    if info.get("_type", "video") != "video" or "entries" in info:
        raise ValueError("Use a single track link, not a playlist or profile.")
    if info.get("is_live") or info.get("live_status") == "is_live":
        raise ValueError("Live streams are not supported. Choose a finished track.")
    duration = info.get("duration")
    if duration is None or duration > MAX_SECONDS:
        raise ValueError("Choose a track with a known duration under 20 minutes.")


class QuietLogger:
    def debug(self, _message: str) -> None:
        pass

    warning = error = debug


def retrieve(url: str, directory: Path) -> dict:
    """Retrieve one bounded source file into an owned temporary directory."""
    import yt_dlp

    url = validate_url(url)

    def progress(data: dict) -> None:
        if data.get("downloaded_bytes", 0) > MAX_BYTES:
            raise ValueError("Source audio exceeds the 50 MB prototype limit.")

    options = {
        "format": "bestaudio[protocol=https]/bestaudio[protocol=http]/bestaudio",
        "outtmpl": str(directory / "source.%(ext)s"),
        "noplaylist": True,
        "extract_flat": "in_playlist",
        "max_filesize": MAX_BYTES,
        "socket_timeout": 20,
        "retries": 1,
        "fragment_retries": 1,
        "concurrent_fragment_downloads": 1,
        "hls_prefer_native": True,
        "js_runtimes": {"node": {}},
        "quiet": True,
        "no_warnings": True,
        "logger": QuietLogger(),
        "progress_hooks": [progress],
        "postprocessors": [],
        "writethumbnail": False,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)
        if not info:
            raise ValueError("No playable audio was found for this link.")
        check_track(info)
        downloader.process_info(info)
        path = Path(downloader.prepare_filename(info)).resolve()
    if path.parent != directory.resolve() or not path.is_file():
        raise ValueError("Source audio could not be retrieved within the 50 MB limit.")
    if path.stat().st_size > MAX_BYTES or path.stat().st_size == 0:
        raise ValueError("Source audio is empty or exceeds the 50 MB limit.")
    return {"filename": path.name, "title": str(info.get("title") or "Audio")[:200]}


def main() -> None:
    try:
        result = retrieve(sys.argv[1], Path(sys.argv[2]))
    except Exception as error:  # noqa: BLE001 -- normalize the extractor boundary
        if isinstance(error, ValueError):
            message = str(error)[:200]
        else:
            message = "The source refused this download or is unavailable. Try another public track."
        print(json.dumps({"error": message}))
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()

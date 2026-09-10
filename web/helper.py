"""Loopback-only source helper for the web prototype, not a public hosting service."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore
from urllib.parse import quote, unquote, urlsplit

from transmute.config import MAX_WORKERS
from web.source import MAX_BYTES, validate_url

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "dist" / "client"
TIMEOUT = 150


class SourceServer(ThreadingHTTPServer):
    def __init__(self, port: int) -> None:
        self.slots = BoundedSemaphore(MAX_WORKERS)
        super().__init__(("127.0.0.1", port), Handler)


def extract_source(url: str, directory: Path) -> dict:
    """Run the extractor with a hard deadline and bounded, normalized errors."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "web.source", url, str(directory)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("Source retrieval timed out. Try a shorter track.") from None
    try:
        metadata = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ValueError("Source retrieval failed. Try another public track.") from None
    if not isinstance(metadata, dict):
        raise ValueError("Source retrieval failed.")
    if result.returncode or metadata.get("error"):
        raise ValueError(str(metadata.get("error") or "Source retrieval failed.")[:200])
    filename = metadata.get("filename")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError("Source retrieval returned an invalid file.")
    path = directory / filename
    if not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError("Source audio is missing or exceeds the 50 MB limit.")
    return metadata


class Handler(BaseHTTPRequestHandler):
    server: SourceServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(180)

    def log_message(self, _format: str, *args: object) -> None:
        pass

    def send_json(self, status: int, data: dict) -> None:
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/api/health":
            self.send_json(200, {"ready": True})
            return
        requested = unquote(urlsplit(self.path).path).lstrip("/") or "index.html"
        path = (STATIC / requested).resolve()
        if not path.is_relative_to(STATIC.resolve()) or not path.is_file():
            self.send_json(404, {"error": "Not found."})
            return
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            with path.open("rb") as asset:
                shutil.copyfileobj(asset, self.wfile)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self) -> None:
        # Custom headers require a CORS preflight from other sites. Host checks
        # also reject DNS rebinding attempts against the loopback listener.
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        preview_host = os.environ.get("TRANSMUTE_PREVIEW_HOST")
        if preview_host:
            hosts.add(preview_host)
        if self.headers.get("X-Transmute-Client") != "web" or self.headers.get("Host") not in hosts:
            self.send_json(403, {"error": "Open Transmute to retrieve audio."})
            return
        if self.path != "/api/source":
            self.send_json(404, {"error": "Not found."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("Paste one track link.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Paste one track link.")
            url = validate_url(payload.get("url"))
        except (ValueError, UnicodeError):
            self.send_json(400, {"error": "Paste one HTTPS YouTube or SoundCloud track link."})
            return
        if not self.server.slots.acquire(blocking=False):
            self.send_json(429, {"error": "All download slots are busy. Try again shortly."})
            return
        try:
            with TemporaryDirectory(prefix="transmute-web-") as temporary:
                directory = Path(temporary)
                try:
                    metadata = extract_source(url, directory)
                except (ValueError, OSError) as error:
                    message = str(error) if isinstance(error, ValueError) else "Could not retrieve audio. Try again."
                    self.send_json(422, {"error": message})
                    return
                path = directory / metadata["filename"]
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("X-Track-Title", quote(str(metadata.get("title") or "Audio")))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with path.open("rb") as audio:
                    shutil.copyfileobj(audio, self.wfile)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            self.server.slots.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    server = SourceServer(args.port)
    print(f"Transmute: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

"""Offline contracts for the local browser prototype's extraction boundary."""

import json
import subprocess
from pathlib import Path
from threading import BoundedSemaphore
from types import SimpleNamespace

import pytest

helper = pytest.importorskip("web.helper", reason="The web prototype is checkout-only")
source = pytest.importorskip("web.source", reason="The web prototype is checkout-only")
extract_source = helper.extract_source
MAX_BYTES = source.MAX_BYTES
check_track = source.check_track
retrieve = source.retrieve
validate_url = source.validate_url


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=x", "https://youtu.be/x",
    "https://soundcloud.com/artist/track", "https://on.soundcloud.com/x",
])
def test_web_accepts_supported_https_sources(url):
    assert validate_url(url) == url


@pytest.mark.parametrize("url", [
    None, [], "file:///tmp/audio", "http://youtube.com/watch?v=x",
    "https://youtube.com.evil.test/x", "https://youtube.com@localhost/x",
    "https://user:secret@youtube.com/x", "https://youtube.com:1234/x",
    "https://youtube.com:bad/x", "https://soundcloud.com/a b",
])
def test_web_rejects_invalid_sources(url):
    with pytest.raises(ValueError):
        validate_url(url)


@pytest.mark.parametrize("info", [
    {"_type": "playlist", "entries": []}, {"duration": 1201},
    {"is_live": True, "duration": 10}, {"duration": None},
])
def test_web_rejects_unbounded_media(info):
    with pytest.raises(ValueError):
        check_track(info)


def test_web_downloads_source_without_transcoding_or_metadata(monkeypatch, tmp_path):
    captured = {}

    class FakeDownloader:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def extract_info(self, url, *, download):
            assert download is False
            return {"duration": 60, "title": "Original title", "ext": "webm"}

        def process_info(self, info):
            (tmp_path / "source.webm").write_bytes(b"source audio")

        def prepare_filename(self, info):
            return str(tmp_path / "source.webm")

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeDownloader)
    result = retrieve("https://youtube.com/watch?v=test", tmp_path)
    assert result == {"filename": "source.webm", "title": "Original title"}
    assert captured["postprocessors"] == []
    assert captured["writethumbnail"] is False
    assert captured["noplaylist"] is True
    assert captured["extract_flat"] == "in_playlist"
    assert captured["max_filesize"] == MAX_BYTES
    assert captured["socket_timeout"] == 20
    with pytest.raises(ValueError, match="50 MB"):
        captured["progress_hooks"][0]({"downloaded_bytes": MAX_BYTES + 1})


def test_web_rejects_playlist_before_downloading(monkeypatch, tmp_path):
    class FakeDownloader:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def extract_info(self, *_args, **_kwargs):
            return {"_type": "playlist", "entries": [{"id": "a"}]}

        def process_info(self, _info):
            pytest.fail("Playlist must not reach download")

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeDownloader)
    with pytest.raises(ValueError, match="single track"):
        retrieve("https://soundcloud.com/artist/sets/album", tmp_path)


def test_web_subprocess_has_deadline_and_returns_file(monkeypatch, tmp_path):
    def run(args, **kwargs):
        assert args[1:3] == ["-m", "web.source"]
        assert kwargs["timeout"] == 150
        assert kwargs["check"] is False
        assert "shell" not in kwargs
        (tmp_path / "source.mp3").write_bytes(b"audio")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"filename": "source.mp3"}))

    monkeypatch.setattr("web.helper.subprocess.run", run)
    assert extract_source("https://youtu.be/test", tmp_path)["filename"] == "source.mp3"


def test_web_subprocess_timeout_is_actionable(monkeypatch, tmp_path):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("extract", 150)

    monkeypatch.setattr("web.helper.subprocess.run", run)
    with pytest.raises(ValueError, match="timed out"):
        extract_source("https://youtu.be/test", tmp_path)


@pytest.mark.parametrize("output", ["not json", "[]", '{"filename":"../escape.mp3"}', '{"filename":"missing.mp3"}'])
def test_web_rejects_bad_worker_results(monkeypatch, tmp_path, output):
    monkeypatch.setattr("web.helper.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=output))
    with pytest.raises(ValueError):
        extract_source("https://youtu.be/test", tmp_path)


def test_web_api_rejects_cross_origin_posts_before_reading():
    from web.helper import Handler

    responses = []
    handler = object.__new__(Handler)
    handler.server = SimpleNamespace(server_port=8766)
    handler.headers = {}
    handler.send_json = lambda status, payload: responses.append((status, payload))
    handler.do_POST()
    assert responses[0][0] == 403


def test_web_api_removes_temporary_source(monkeypatch):
    from io import BytesIO

    from web.helper import Handler

    directories = []

    def source(url, directory):
        directories.append(directory)
        (directory / "source.webm").write_bytes(b"audio")
        return {"filename": "source.webm", "title": "Song"}

    monkeypatch.setattr("web.helper.extract_source", source)
    body = json.dumps({"url": "https://youtu.be/test"}).encode()
    handler = object.__new__(Handler)
    handler.server = SimpleNamespace(server_port=8766, slots=BoundedSemaphore(1))
    handler.headers = {"X-Transmute-Client": "web", "Content-Length": str(len(body)), "Host": "127.0.0.1:8766"}
    handler.path = "/api/source"
    handler.rfile = BytesIO(body)
    handler.wfile = BytesIO()
    status = []
    handler.send_response = status.append
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    handler.do_POST()
    assert status == [200]
    assert handler.wfile.getvalue() == b"audio"
    assert not Path(directories[0]).exists()

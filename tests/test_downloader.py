from transmute.config import Settings
from transmute.downloader import Job, download_job, extract_urls


def test_single_url():
    assert extract_urls("https://youtube.com/watch?v=x") == [
        "https://youtube.com/watch?v=x"
    ]


def test_urls_in_prose():
    urls = extract_urls("check https://a.com/1 and https://b.com/2 out")
    assert urls == ["https://a.com/1", "https://b.com/2"]


def test_concatenated_urls_split():
    urls = extract_urls("https://a.com/onehttps://b.com/two")
    assert urls == ["https://a.com/one", "https://b.com/two"]


def test_no_urls():
    assert extract_urls("not a link") == []


def test_download_options_preserve_local_audio_safety(monkeypatch, tmp_path):
    captured = {}
    output = tmp_path / "Artist - Song.mp3"

    class FakeYoutubeDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, url, *, download):
            assert url == "https://example.com/song"
            assert download is True
            return {
                "title": "Song",
                "uploader": "Artist",
                "requested_downloads": [{"filepath": str(output)}],
            }

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYoutubeDL)
    job = download_job(
        Job(url="https://example.com/song"),
        Settings(out_dir=tmp_path, quality="256"),
    )

    assert job.status == "done"
    assert job.path == output
    assert captured["format"] == "bestaudio/best"
    assert captured["noplaylist"] is True
    assert captured["writethumbnail"] is True
    assert captured["postprocessors"] == [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "256",
        },
        {"key": "FFmpegMetadata"},
        {"key": "EmbedThumbnail"},
    ]


def test_output_directory_error_is_normalized(monkeypatch, tmp_path):
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("occupied", encoding="utf-8")
    monkeypatch.setattr(
        "yt_dlp.YoutubeDL",
        lambda _options: (_ for _ in ()).throw(
            AssertionError("yt-dlp should not start when output setup fails")
        ),
    )

    job = download_job(
        Job(url="https://example.com/song"),
        Settings(out_dir=parent_file / "music"),
    )

    assert job.status == "error"
    assert job.error
    assert "\n" not in job.error

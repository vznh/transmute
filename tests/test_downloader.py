from transmute.downloader import classify_error, extract_urls, is_supported_url


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


def test_supported_youtube_and_soundcloud():
    assert is_supported_url("https://youtube.com/watch?v=x")
    assert is_supported_url("https://www.youtube.com/watch?v=x")
    assert is_supported_url("https://m.youtube.com/watch?v=x")
    assert is_supported_url("https://music.youtube.com/watch?v=x")
    assert is_supported_url("https://youtu.be/x")
    assert is_supported_url("https://soundcloud.com/artist/track")
    assert is_supported_url("https://on.soundcloud.com/abc")


def test_unsupported_hosts_rejected():
    assert not is_supported_url("https://vimeo.com/123")
    assert not is_supported_url("https://example.com/song.mp3")
    assert not is_supported_url("https://notyoutube.com/watch?v=x")
    assert not is_supported_url("https://youtube.com.evil.com/x")


def test_classify_strips_ansi_codes():
    e = Exception("\x1b[0;31mERROR:\x1b[0m Unsupported URL: https://github.com/x")
    summary, detail, retryable = classify_error(e, "https://github.com/x")
    assert "\x1b" not in summary and "\x1b" not in detail
    assert summary == "github.com isn't a supported site"
    assert not retryable


def test_classify_unavailable_not_retryable():
    summary, _, retryable = classify_error(
        Exception("ERROR: [youtube] abc: Video unavailable"), "https://youtu.be/abc"
    )
    assert summary == "video unavailable"
    assert not retryable


def test_classify_network_retryable():
    summary, _, retryable = classify_error(
        Exception("ERROR: Unable to download webpage: timed out"), "https://a.com/1"
    )
    assert summary.startswith("network error")
    assert retryable


def test_classify_unknown_keeps_first_line_and_retries():
    e = Exception("ERROR: something odd happened\nmore detail here")
    summary, detail, retryable = classify_error(e, "https://a.com/1")
    assert summary == "something odd happened"
    assert "more detail here" in detail
    assert retryable

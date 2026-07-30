from transmute.downloader import extract_urls, is_supported_url


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

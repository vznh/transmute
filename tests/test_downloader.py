from transmute.downloader import extract_urls


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

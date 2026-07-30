from transmute.enrich import TrackTags, _safe_filename


def test_safe_filename_strips_reserved_chars():
    assert _safe_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"


def test_safe_filename_plain():
    assert _safe_filename("Artist - Title") == "Artist - Title"


def test_tracktags_defaults():
    t = TrackTags(artist="X", title="Y")
    assert t.album is None and t.confidence is None and t.kind is None

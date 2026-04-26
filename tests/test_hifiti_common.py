from hifiti_common import mask, snippet


def test_mask_email_short_local_part():
    assert mask("ab@x.com") == "a***@x.com"


def test_mask_email_normal():
    # Use 2-char prefix to match Phase 1 hifiti_sign.py:220
    assert mask("collin@example.com") == "co***@example.com"


def test_mask_username_short():
    assert mask("xy") == "x***"


def test_mask_username_normal():
    assert mask("collin") == "col***"


def test_snippet_truncates_to_500_chars():
    text = "a" * 800
    result = snippet(text)
    assert len(result) == 500


def test_snippet_strips_newlines():
    assert snippet("hello\nworld\n") == "hello world"


def test_snippet_handles_empty_or_none():
    assert snippet("") == ""
    assert snippet(None) == ""

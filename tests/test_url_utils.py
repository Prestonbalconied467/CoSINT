def test_is_likely_domain_accepts_common_domains():
    import shared.url_utils as u

    assert u.is_likely_domain("example.com")
    assert u.is_likely_domain("sub.example.co.uk")
    assert u.is_likely_domain("EXAMPLE.COM")


def test_is_likely_domain_rejects_invalid_inputs():
    import shared.url_utils as u

    assert not u.is_likely_domain("")
    assert not u.is_likely_domain("not a domain")
    assert not u.is_likely_domain("localhost")
    # file-like hashy names should be rejected as pivot domains
    assert not u.is_likely_domain("deadbeef.png")


def test_strip_to_host_and_extract_domain_various_inputs():
    import shared.url_utils as u

    assert u.extract_domain("https://example.com/path") == "example.com"
    assert u.extract_domain("http://www.EXAMPLE.com") == "example.com"
    assert u.extract_domain("//example.com/rel") == "example.com"
    assert u.extract_domain("example.com:8080/page") == "example.com"
    # non-domain returns empty
    assert u.extract_domain("/not/a/url") == ""


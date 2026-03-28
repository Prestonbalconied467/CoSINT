def test_normalize_url_adds_scheme_when_missing():
    import tools.helper.scraper_utils as s

    assert s.normalize_url("example.com") == "https://example.com"
    assert s.normalize_url("https://a.com") == "https://a.com"


def test_to_text_without_bs4_strips_tags():
    import tools.helper.scraper_utils as s

    html = "<html><head></head><body><h1>Hi</h1><script>bad()</script></body></html>"
    text = s.to_text(html)
    assert "Hi" in text


def test_extract_emails_and_phones_and_socials():
    import tools.helper.scraper_utils as s

    text = "Contact me at Test@Example.com or admin@example.org. Phone: +41 (0) 78 927 2696"
    emails = s.extract_emails(text)
    # example.com is in ignore list, so only admin@example.org is expected
    assert "admin@example.org" in emails

    phones = s.extract_phones(text)
    assert any("927" in p for p in phones)

    html = "https://twitter.com/alice and https://github.com/bob"
    socials = s.extract_socials(html)
    assert "twitter/x" in socials and "alice" in socials["twitter/x"]
    assert "github" in socials and "bob" in socials["github"]


def test_find_contact_and_all_links_filters_by_domain():
    import tools.helper.scraper_utils as s

    base = "https://example.com/path"
    html = '<a href="/contact">Contact</a><a href="/about">About</a><a href="https://other.com/x">Other</a>'
    contacts = s.find_contact_links(html, base, "example.com")
    assert any("contact" in c for c in contacts)

    all_links = s.find_all_links(html, base, "example.com")
    assert any("about" in a for a in all_links)


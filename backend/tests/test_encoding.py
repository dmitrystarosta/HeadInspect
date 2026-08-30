"""Tests for decode_html/sniff_charset (item 4 of the timeout/encoding
task): HTML used to be decoded unconditionally as UTF-8
(`content.decode("utf-8", errors="replace")`), which silently turned any
Windows-1251 (or other non-UTF-8) Russian page's Cyrillic text into
replacement characters - a real, previously-undetected bug found while
investigating leonidagutin.ru.

Priority order (documented in htmlmeta.py::sniff_charset and verified here):
  1. charset from the HTTP Content-Type response header;
  2. <meta charset="..."> in the document;
  3. the older <meta http-equiv="Content-Type" content="...charset=...">;
  4. UTF-8 fallback if nothing was declared.
If the HTTP header and an in-document <meta> disagree, the HTTP header
wins outright (matches real browser behavior) - even if that means an
inconsistent server produces mangled text, that is what the server itself
declared to be authoritative.
"""
from __future__ import annotations

from app.htmlmeta import MetadataParser, decode_html, sniff_charset

RU_TITLE = "Заголовок страницы"
RU_BODY = "Обычный русский текст на странице сайта."


def _page(title=RU_TITLE, body=RU_BODY, extra_head=""):
    return f"<html><head>{extra_head}<title>{title}</title></head><body>{body}</body></html>"


def test_utf8_page_is_unaffected_by_the_fix():
    html = _page()
    content = html.encode("utf-8")
    assert decode_html(content, "text/html; charset=utf-8") == html
    # Also with no header at all - UTF-8 remains the sane default.
    assert decode_html(content, None) == html


def test_windows_1251_from_http_content_type_header():
    html = _page()
    content = html.encode("windows-1251")
    decoded = decode_html(content, "text/html; charset=windows-1251")
    assert RU_TITLE in decoded
    assert RU_BODY in decoded


def test_windows_1251_from_meta_charset_tag():
    html = _page(extra_head='<meta charset="windows-1251">')
    content = html.encode("windows-1251")
    # No HTTP header at all - must fall through to the <meta charset> tag.
    decoded = decode_html(content, None)
    assert RU_TITLE in decoded


def test_windows_1251_from_old_http_equiv_form():
    html = _page(extra_head='<meta http-equiv="Content-Type" content="text/html; charset=windows-1251">')
    content = html.encode("windows-1251")
    decoded = decode_html(content, None)
    assert RU_TITLE in decoded


def test_missing_charset_anywhere_falls_back_to_utf8():
    html = _page()
    content = html.encode("utf-8")
    # No header, no meta charset at all.
    decoded = decode_html(content, None)
    assert decoded == html


def test_http_header_wins_over_conflicting_meta_charset():
    """Documented priority rule: if the HTTP header and <meta charset>
    disagree, the header wins - even though this specific (self-
    contradictory) response then produces unreadable text, because the
    header is what the server declared authoritative. This mirrors real
    browser behavior and is a deliberate, documented choice, not a bug."""
    html = _page(extra_head='<meta charset="windows-1251">')
    content = html.encode("windows-1251")
    # Header claims utf-8, but the bytes are actually windows-1251.
    decoded = decode_html(content, "text/html; charset=utf-8")
    assert RU_TITLE not in decoded  # header (wrong) was honored, not meta (correct)
    assert decoded.count("\ufffd") > 0  # utf-8 decode of cp1251 bytes hits invalid sequences, replaced safely


def test_unknown_charset_name_does_not_crash_the_service():
    content = "<html><title>ok</title></html>".encode("utf-8")
    # Must never raise - an invalid/unknown codec name from a broken server
    # falls back to UTF-8 with replacement characters instead of crashing
    # the audit.
    decoded = decode_html(content, "text/html; charset=totally-not-a-real-charset")
    assert "ok" in decoded


def test_declared_charset_that_does_not_actually_match_the_bytes_falls_back_safely():
    """A server can declare a charset that doesn't actually decode the
    bytes it sent (a different kind of broken response than an unknown
    codec name) - must not raise either."""
    content_with_high_byte = b"<html><title>ok\xff</title></html>"
    decoded = decode_html(content_with_high_byte, "text/html; charset=ascii")
    assert "ok" in decoded  # never raises, degrades gracefully


def test_cyrillic_title_and_meta_description_survive_full_metadata_parsing():
    """End-to-end: decode_html's output must be immediately usable by
    MetadataParser, producing readable (not mangled) title/description -
    this is the actual, user-visible fix for leonidagutin.ru."""
    html = (
        '<html><head><meta charset="windows-1251">'
        '<title>Ремонт квартир под ключ в Москве</title>'
        '<meta name="description" content="Качественный ремонт квартир и офисов.">'
        "</head><body>Текст страницы на русском языке.</body></html>"
    )
    content = html.encode("windows-1251")
    decoded = decode_html(content, None)

    parser = MetadataParser()
    parser.feed(decoded)

    assert parser.title == "Ремонт квартир под ключ в Москве"
    assert parser.meta_description == "Качественный ремонт квартир и офисов."
    assert "\ufffd" not in (parser.title or "")
    assert "\ufffd" not in (parser.meta_description or "")


def test_sniff_charset_only_scans_a_bounded_prefix_not_the_whole_document():
    """No heavy full-document scan - a charset declaration far past the
    HTML5-mandated first-1024-bytes convention (here: past the 4096-byte
    sniff window) is simply not found, falling back to UTF-8. This is a
    deliberate simplicity trade-off (task: 'тяжёлую эвристику не нужно'),
    not an oversight - documented here so it isn't "fixed" by accident."""
    padding = "<!-- " + ("x" * 5000) + " -->"
    html = f"<html><head>{padding}<meta charset=\"windows-1251\"></head></html>"
    content = html.encode("windows-1251")
    charset = sniff_charset(content, None)
    assert charset == "utf-8"  # the meta tag was too far in to be sniffed


def test_meta_charset_within_html5_recommended_window_is_found():
    html = '<html><head><meta charset="windows-1251"><title>Т</title></head></html>'
    content = html.encode("windows-1251")
    assert sniff_charset(content, None) == "windows-1251"


def test_first_meta_charset_declaration_wins_if_document_has_more_than_one():
    """Matches the HTML5 spec: only the first charset declaration counts -
    MetadataParser already enforces this (self.charset is set-once)."""
    html = (
        '<html><head><meta charset="windows-1251">'
        '<meta charset="koi8-r">'
        "</head></html>"
    )
    content = html.encode("windows-1251")
    assert sniff_charset(content, None) == "windows-1251"


async def test_end_to_end_analyze_page_produces_readable_cyrillic_for_windows_1251_site(monkeypatch):
    """Full integration: analyze_page (the real function, not just
    decode_html in isolation) must produce a readable PageResult.title for
    a Windows-1251 site - this is the exact leonidagutin.ru-shaped
    scenario, proving the fix through the actual code path used in
    production, not just the underlying helper.
    """
    import asyncio
    from app import audit as audit_module

    html = (
        '<html><head><meta charset="windows-1251">'
        "<title>Ремонт квартир под ключ</title></head>"
        "<body>Страница на русском языке.</body></html>"
    )
    content = html.encode("windows-1251")

    class FakeFetchResult:
        def __init__(self):
            self.url = "https://leonidagutin.ru/"
            self.status_code = 200
            self.headers = {"content-type": "text/html"}  # no charset in the header itself
            self.content = content

    async def fake_safe_fetch(url, **kwargs):
        return FakeFetchResult()

    monkeypatch.setattr(audit_module, "safe_fetch", fake_safe_fetch)

    result = await audit_module.analyze_page("https://leonidagutin.ru/", asyncio.Semaphore(1))

    assert result.check_failed is False
    assert result.title == "Ремонт квартир под ключ"
    assert "\ufffd" not in (result.title or "")

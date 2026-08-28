"""Item 6: www/non-www handling. Uses the *actual* post-redirect host of the
entry page (never an eTLD+1 same-site guess), so shop./forum./admin.
subdomains stay excluded while a genuine www<->non-www redirect on the
homepage is honoured in either direction.
"""
from __future__ import annotations

from dataclasses import dataclass

from app import audit, sitemap


@dataclass
class FakeFetchResult:
    url: str
    status_code: int = 200
    headers: dict | None = None
    content: bytes = b""

    def __post_init__(self):
        if self.headers is None:
            self.headers = {}


SITEMAP_XML = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.example.ru/</loc></url>
  <url><loc>https://www.example.ru/about</loc></url>
</urlset>"""


async def test_discover_urls_accepts_the_explicit_site_host(monkeypatch):
    async def fake_validate(url):
        return url

    async def fake_fetch(url, **kwargs):
        return FakeFetchResult(url=url, content=SITEMAP_XML, headers={"content-type": "application/xml"})

    monkeypatch.setattr(sitemap, "validate_public_url", fake_validate)
    monkeypatch.setattr(sitemap, "safe_fetch", fake_fetch)

    pages, processed, limited, issues = await sitemap.discover_urls(
        "https://example.ru/",
        ["https://www.example.ru/sitemap.xml"],
        site_host="www.example.ru",
    )

    assert pages == ["https://www.example.ru/", "https://www.example.ru/about"]
    assert issues == []


async def test_discover_urls_rejects_unrelated_subdomain_even_with_matching_etld1(monkeypatch):
    shop_sitemap = b"""<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://shop.example.ru/cart</loc></url>
    </urlset>"""

    async def fake_validate(url):
        return url

    async def fake_fetch(url, **kwargs):
        return FakeFetchResult(url=url, content=shop_sitemap, headers={"content-type": "application/xml"})

    monkeypatch.setattr(sitemap, "validate_public_url", fake_validate)
    monkeypatch.setattr(sitemap, "safe_fetch", fake_fetch)

    # Even though shop.example.ru shares the eTLD+1 with example.ru, it must
    # NOT be treated as the same site just because of that.
    pages, processed, limited, issues = await sitemap.discover_urls(
        "https://example.ru/",
        ["https://shop.example.ru/sitemap.xml"],
        site_host="example.ru",
    )

    assert pages == []


async def test_discover_audit_urls_uses_entry_pages_actual_redirect_target_as_site_host(monkeypatch):
    """example.ru redirects (ordinary HTTP redirect, already SSRF-validated
    by safe_fetch) to www.example.ru - sitemap entries on www.example.ru
    must then be accepted, even though the user typed the bare domain.
    """
    async def fake_validate_public_url(url):
        return url

    async def fake_safe_fetch(url, **kwargs):
        if "sitemap" in url:
            return FakeFetchResult(url=url, content=SITEMAP_XML, headers={"content-type": "application/xml"})
        # The entry page itself: reports having landed on www.example.ru
        # after following a same-site redirect (this is exactly what
        # fetcher.safe_fetch's FetchResult.url now reports post-fix).
        return FakeFetchResult(url="https://www.example.ru/", content=b"<html></html>", headers={"content-type": "text/html"})

    async def fake_fetch_robots(url):
        return "https://www.example.ru/robots.txt", False, ""

    monkeypatch.setattr(audit, "validate_public_url", fake_validate_public_url)
    monkeypatch.setattr(audit, "safe_fetch", fake_safe_fetch)
    monkeypatch.setattr(audit, "fetch_robots", fake_fetch_robots)
    # discover_audit_urls delegates sitemap fetching to sitemap.discover_urls,
    # which uses *its own* module-level safe_fetch/validate_public_url
    # bindings (imported separately in sitemap.py) - both must be patched.
    monkeypatch.setattr(sitemap, "validate_public_url", fake_validate_public_url)
    monkeypatch.setattr(sitemap, "safe_fetch", fake_safe_fetch)

    discovered = await audit.discover_audit_urls("https://example.ru/")

    assert discovered["access_blocked_status"] is None
    assert "https://www.example.ru/" in discovered["urls"]
    assert "https://www.example.ru/about" in discovered["urls"]

"""Declared-sitemap fetch failures must be reported (discovery-instability fix).

Previously, a sitemap declared in robots.txt that could not be fetched
(timeout, connect error, 404) was swallowed with a bare `continue`, and
discover_audit_urls then substituted [normalized] - so a transient network
failure looked exactly like a healthy one-page site. Now:
  * a fetch failure / non-200 for a *declared* (robots.txt) sitemap is
    recorded in `issues`;
  * a miss on a *guessed* default location (used only when robots declared
    none) stays silent, as before.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from app import sitemap


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
  <url><loc>https://example.ru/</loc></url>
  <url><loc>https://example.ru/about</loc></url>
</urlset>"""


async def _identity_validate(url):
    return url


async def test_declared_sitemap_fetch_failure_is_recorded(monkeypatch):
    async def failing_fetch(url, **kwargs):
        raise HTTPException(status_code=504, detail=f"Timeout while fetching {url}")

    monkeypatch.setattr(sitemap, "validate_public_url", _identity_validate)
    monkeypatch.setattr(sitemap, "safe_fetch", failing_fetch)

    pages, processed, limited, issues = await sitemap.discover_urls(
        "https://example.ru/",
        ["https://example.ru/sitemap.xml"],  # declared in robots.txt
        site_host="example.ru",
    )

    assert pages == []
    assert processed == []
    assert len(issues) == 1
    assert "sitemap.xml" in issues[0]
    assert "не удалось получить" in issues[0]


async def test_guessed_default_sitemap_miss_stays_silent(monkeypatch):
    async def failing_fetch(url, **kwargs):
        raise HTTPException(status_code=504, detail="Timeout")

    monkeypatch.setattr(sitemap, "validate_public_url", _identity_validate)
    monkeypatch.setattr(sitemap, "safe_fetch", failing_fetch)

    # No declared sitemaps -> discover_urls guesses /sitemap.xml and
    # /sitemap_index.xml. Misses on those must NOT be reported as issues.
    pages, processed, limited, issues = await sitemap.discover_urls(
        "https://example.ru/",
        [],
        site_host="example.ru",
    )

    assert pages == []
    assert issues == []


async def test_declared_sitemap_non_200_is_recorded(monkeypatch):
    async def not_found_fetch(url, **kwargs):
        return FakeFetchResult(url=url, status_code=404, headers={"content-type": "text/html"})

    monkeypatch.setattr(sitemap, "validate_public_url", _identity_validate)
    monkeypatch.setattr(sitemap, "safe_fetch", not_found_fetch)

    pages, processed, limited, issues = await sitemap.discover_urls(
        "https://example.ru/",
        ["https://example.ru/sitemap.xml"],
        site_host="example.ru",
    )

    assert pages == []
    assert len(issues) == 1
    assert "HTTP 404" in issues[0]


async def test_declared_sitemap_success_still_reports_no_issue(monkeypatch):
    async def ok_fetch(url, **kwargs):
        return FakeFetchResult(url=url, content=SITEMAP_XML, headers={"content-type": "application/xml"})

    monkeypatch.setattr(sitemap, "validate_public_url", _identity_validate)
    monkeypatch.setattr(sitemap, "safe_fetch", ok_fetch)

    pages, processed, limited, issues = await sitemap.discover_urls(
        "https://example.ru/",
        ["https://example.ru/sitemap.xml"],
        site_host="example.ru",
    )

    assert pages == ["https://example.ru/", "https://example.ru/about"]
    assert issues == []


async def test_declared_index_child_failure_is_reported(monkeypatch):
    index_xml = b"""<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.ru/child.xml</loc></sitemap>
    </sitemapindex>"""

    async def fetch(url, **kwargs):
        if url.endswith("/sitemap.xml"):
            return FakeFetchResult(url=url, content=index_xml, headers={"content-type": "application/xml"})
        # The child sitemap of a declared index fails to load:
        raise HTTPException(status_code=502, detail="Cannot fetch")

    monkeypatch.setattr(sitemap, "validate_public_url", _identity_validate)
    monkeypatch.setattr(sitemap, "safe_fetch", fetch)

    pages, processed, limited, issues = await sitemap.discover_urls(
        "https://example.ru/",
        ["https://example.ru/sitemap.xml"],
        site_host="example.ru",
    )

    assert any("child.xml" in issue for issue in issues)

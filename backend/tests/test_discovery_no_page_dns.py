"""Discovery no longer DNS-resolves every sitemap page URL.

`discover_urls` used to call `validate_public_url(page_url)` (which does a
DNS resolve + IP validation) for every one of up to MAX_AUDIT_URLS entries,
sequentially, and then discarded the resolved addresses - safe_fetch resolves
and validates again at connect time anyway. The per-page resolve was replaced
with the syntactic-only `normalize_public_url`.

These tests lock two properties:
  1. ordinary same-host sitemap URLs are still discovered exactly as before,
     and no per-page DNS validation happens during discovery;
  2. connect-time SSRF protection is unchanged: safe_fetch still refuses to
     connect to a private/forbidden IP, and such a rejection surfaces as an
     ordinary check_failed page rather than breaking the job.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlsplit

import pytest
from fastapi import HTTPException

from app import audit, fetcher, sitemap


@dataclass
class FakeFetchResult:
    url: str
    status_code: int = 200
    headers: dict | None = None
    content: bytes = b""

    def __post_init__(self):
        if self.headers is None:
            self.headers = {}


# A urlset mixing same-host pages, a cross-host page (must be filtered by the
# host check), and a same-host page that would NOT resolve in DNS ("/ghost").
# After the change the unresolvable same-host page is kept here - discovery no
# longer resolves it - and would only fail later, in safe_fetch, if fetched.
MIXED_URLSET = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.ru/</loc></url>
  <url><loc>https://example.ru/about</loc></url>
  <url><loc>https://other.ru/elsewhere</loc></url>
  <url><loc>https://example.ru/ghost</loc></url>
</urlset>"""


async def test_same_host_urls_discovered_without_per_page_dns(monkeypatch):
    async def ok_fetch(url, **kwargs):
        return FakeFetchResult(
            url=url, content=MIXED_URLSET, headers={"content-type": "application/xml"}
        )

    # Record every URL that still goes through validate_public_url (the DNS
    # path). It must be called for the sitemap URL only - never once per page.
    validated: list[str] = []

    async def recording_validate(url):
        validated.append(url)
        return url

    monkeypatch.setattr(sitemap, "safe_fetch", ok_fetch)
    monkeypatch.setattr(sitemap, "validate_public_url", recording_validate)

    pages, processed, limited, issues = await sitemap.discover_urls(
        "https://example.ru/",
        ["https://example.ru/sitemap.xml"],  # declared in robots.txt
        site_host="example.ru",
    )

    # Same-host pages kept (including the unresolvable /ghost - now a syntactic
    # decision, not a DNS one); cross-host page dropped by the host filter.
    assert pages == [
        "https://example.ru/",
        "https://example.ru/about",
        "https://example.ru/ghost",
    ]
    assert "https://other.ru/elsewhere" not in pages
    assert issues == []

    # The heart of the change: DNS validation ran once (for the sitemap URL),
    # NOT once per page. Before the fix this list would also contain every
    # page URL.
    assert validated == ["https://example.ru/sitemap.xml"]


async def test_cross_host_pages_are_still_filtered(monkeypatch):
    async def ok_fetch(url, **kwargs):
        return FakeFetchResult(
            url=url, content=MIXED_URLSET, headers={"content-type": "application/xml"}
        )

    async def identity_validate(url):
        return url

    monkeypatch.setattr(sitemap, "safe_fetch", ok_fetch)
    monkeypatch.setattr(sitemap, "validate_public_url", identity_validate)

    pages, _processed, _limited, _issues = await sitemap.discover_urls(
        "https://example.ru/",
        ["https://example.ru/sitemap.xml"],
        site_host="example.ru",
    )

    assert all(urlsplit(p).hostname == "example.ru" for p in pages)
    assert not any("other.ru" in p for p in pages)


# --- Connect-time SSRF is unchanged and authoritative in safe_fetch ---------


class _FakeResponse:
    def __init__(self, status_code=200, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    async def aiter_bytes(self):
        yield self._body

    async def aclose(self):
        pass


class _RecordingClient:
    """Records requests so a test can assert that none were ever sent."""

    def __init__(self, *a, **kw):
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def build_request(self, method, url, headers=None, extensions=None):
        self.requests.append(url)
        return {"url": url}

    async def send(self, request, stream=False):
        return _FakeResponse(200, headers={"content-type": "text/html"}, body=b"hi")


async def test_safe_fetch_refuses_private_ip_and_never_connects(monkeypatch):
    """A sitemap page whose host resolves to a private IP can now reach
    safe_fetch (discovery no longer pre-filters it). safe_fetch must still
    resolve, see the forbidden IP, and refuse - without ever building a
    request / opening a connection."""

    async def fake_getaddrinfo(host, port, type=None):
        # Hostile/misconfigured host that resolves to an internal address.
        return [(None, None, None, None, ("10.0.0.5", 443))]

    class FakeLoop:
        def getaddrinfo(self, host, port, type=None):
            return fake_getaddrinfo(host, port, type=type)

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())

    client_holder = {}

    def client_factory(*a, **kw):
        client = _RecordingClient()
        client_holder["client"] = client
        return client

    monkeypatch.setattr(fetcher.httpx, "AsyncClient", client_factory)

    with pytest.raises(HTTPException) as exc_info:
        await fetcher.safe_fetch("https://internal.example/page", max_bytes=1_000_000)

    assert exc_info.value.status_code == 400
    # The crucial guarantee: resolution/validation happened BEFORE any
    # connection, so no request was ever built or sent to the forbidden IP.
    assert client_holder["client"].requests == []


async def test_forbidden_ip_page_becomes_check_failed_not_a_job_break(monkeypatch):
    """End of the chain: a URL that safe_fetch rejects (e.g. resolves to a
    forbidden IP) must be reported as an ordinary check_failed PageResult,
    never propagate as an exception that would abort the whole audit."""

    async def rejecting_fetch(url, **kwargs):
        raise HTTPException(
            status_code=400,
            detail="Hostname resolves to a private, local, or otherwise forbidden IP address",
        )

    monkeypatch.setattr(audit, "safe_fetch", rejecting_fetch)

    semaphore = asyncio.Semaphore(1)
    result = await audit.analyze_page("https://internal.example/page", semaphore)

    assert result is not None
    assert result.check_failed is True
    assert result.check_reason == "network"
    assert result.status_code is None

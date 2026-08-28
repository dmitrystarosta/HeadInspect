"""Item 1: proves fetcher.safe_fetch actually *uses* the address returned by
resolve_and_validate_host for the real TCP connection (rather than letting
httpx do its own, second, unpinned DNS resolution), on every redirect hop,
while keeping Host/SNI and the logical (hostname-based) reported URL
correct. Uses a fake httpx.AsyncClient so no real network access is
required - the fake is a drop-in for the same build_request/send interface
fetcher.py calls.
"""
from __future__ import annotations

import pytest

from app import fetcher


class FakeResponse:
    def __init__(self, status_code=200, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    async def aiter_bytes(self):
        yield self._body

    async def aclose(self):
        pass


class FakeRequest:
    def __init__(self, method, url, headers, extensions):
        self.method = method
        self.url = url
        self.headers = headers
        self.extensions = extensions


class FakeAsyncClient:
    """Records every request built and lets the test script the response."""

    def __init__(self, send_handler, *a, **kw):
        self._send_handler = send_handler
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def build_request(self, method, url, headers=None, extensions=None):
        req = FakeRequest(method, url, headers, extensions)
        self.requests.append(req)
        return req

    async def send(self, request, stream=False):
        return self._send_handler(request)


async def test_safe_fetch_pins_the_validated_ip_and_preserves_host_and_sni(monkeypatch):
    async def fake_resolve(url):
        return ["203.0.113.10"]

    monkeypatch.setattr(fetcher, "resolve_and_validate_host", fake_resolve)

    client_holder = {}

    def send_handler(request):
        return FakeResponse(200, headers={"content-type": "text/html"}, body=b"hello")

    def client_factory(*a, **kw):
        client = FakeAsyncClient(send_handler)
        client_holder["client"] = client
        return client

    monkeypatch.setattr(fetcher.httpx, "AsyncClient", client_factory)

    result = await fetcher.safe_fetch("https://example.ru/page", max_bytes=1_000_000)

    assert result.content == b"hello"
    reqs = client_holder["client"].requests
    assert len(reqs) == 1
    req = reqs[0]

    assert req.url == "https://203.0.113.10/page", "must connect to the pinned IP, not the hostname"
    assert req.headers.get("Host") == "example.ru"
    assert req.extensions.get("sni_hostname") == "example.ru"

    # Everything downstream (dedup, sitemap display, Meta/OG/Schema) depends
    # on FetchResult.url being the real hostname, never the IP literal we
    # actually connected to.
    assert result.url == "https://example.ru/page"


async def test_safe_fetch_revalidates_and_repins_on_every_redirect_hop(monkeypatch):
    resolve_calls = []

    async def fake_resolve(url):
        resolve_calls.append(url)
        return ["203.0.113.20"] if "www.example.ru" in url else ["203.0.113.10"]

    monkeypatch.setattr(fetcher, "resolve_and_validate_host", fake_resolve)

    def send_handler(request):
        if request.headers.get("Host") == "example.ru":
            return FakeResponse(301, headers={"location": "https://www.example.ru/page"})
        return FakeResponse(200, headers={"content-type": "text/html"}, body=b"final")

    client_holder = {}

    def client_factory(*a, **kw):
        client = FakeAsyncClient(send_handler)
        client_holder["client"] = client
        return client

    monkeypatch.setattr(fetcher.httpx, "AsyncClient", client_factory)

    result = await fetcher.safe_fetch("https://example.ru/page", max_bytes=1_000_000)

    assert len(resolve_calls) == 2, "each hop must be independently resolved+validated"
    assert "example.ru" in resolve_calls[0]
    assert "www.example.ru" in resolve_calls[1]

    reqs = client_holder["client"].requests
    assert len(reqs) == 2
    assert reqs[0].url == "https://203.0.113.10/page"
    assert reqs[1].url == "https://203.0.113.20/page"
    assert reqs[1].headers.get("Host") == "www.example.ru"

    assert result.url == "https://www.example.ru/page"
    assert result.content == b"final"

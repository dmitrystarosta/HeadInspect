"""safe_fetch address fallback (discovery-instability fix).

resolve_and_validate_host() returns *all* validated public IPs for a host, but
safe_fetch used only pinned_ips[0] with no fallback and no retry - so a single
transient ConnectTimeout to one anycast edge (e.g. one of GitHub Pages' four
185.199.108-111.153 IPs) failed the whole fetch. safe_fetch now falls back to
the next already-validated address, but ONLY on connection-establishment
failure, and NEVER re-resolves (the DNS-pinning / SSRF guarantee is preserved).

These tests use a fake httpx.AsyncClient (no real network) whose send() is
scripted per pinned IP so we can drive connect failures deterministically.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import httpx
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


def _ip_of(request) -> str:
    return urlsplit(str(request.url)).hostname


def _install(monkeypatch, ips, send_handler):
    async def fake_resolve(url):
        return list(ips)

    monkeypatch.setattr(fetcher, "resolve_and_validate_host", fake_resolve)

    holder = {}

    def factory(*a, **kw):
        client = FakeAsyncClient(send_handler)
        holder["client"] = client
        return client

    monkeypatch.setattr(fetcher.httpx, "AsyncClient", factory)
    return holder


THREE_IPS = ["185.199.108.153", "185.199.109.153", "185.199.110.153"]


async def test_first_ip_connect_timeout_falls_back_to_second(monkeypatch):
    attempts = []

    def send_handler(request):
        ip = _ip_of(request)
        attempts.append(ip)
        if ip == THREE_IPS[0]:
            raise httpx.ConnectTimeout("connect timed out")
        return FakeResponse(200, headers={"content-type": "text/html"}, body=b"ok")

    _install(monkeypatch, THREE_IPS, send_handler)
    result = await fetcher.safe_fetch("https://headinspect.ru/", max_bytes=1_000_000)

    assert result.content == b"ok"
    assert attempts == [THREE_IPS[0], THREE_IPS[1]]  # fell back exactly once


async def test_first_ip_connect_error_falls_back_to_second(monkeypatch):
    attempts = []

    def send_handler(request):
        ip = _ip_of(request)
        attempts.append(ip)
        if ip == THREE_IPS[0]:
            raise httpx.ConnectError("connection refused")
        return FakeResponse(200, headers={"content-type": "text/html"}, body=b"ok")

    _install(monkeypatch, THREE_IPS, send_handler)
    result = await fetcher.safe_fetch("https://headinspect.ru/", max_bytes=1_000_000)

    assert result.content == b"ok"
    assert attempts == [THREE_IPS[0], THREE_IPS[1]]


async def test_all_ips_connect_timeout_raise_504(monkeypatch):
    attempts = []

    def send_handler(request):
        attempts.append(_ip_of(request))
        raise httpx.ConnectTimeout("connect timed out")

    _install(monkeypatch, THREE_IPS, send_handler)
    with pytest.raises(fetcher.HTTPException) as exc:
        await fetcher.safe_fetch("https://headinspect.ru/", max_bytes=1_000_000)

    assert exc.value.status_code == 504
    assert attempts == THREE_IPS  # every validated address was tried


async def test_all_ips_connect_error_raise_502(monkeypatch):
    def send_handler(request):
        raise httpx.ConnectError("refused")

    _install(monkeypatch, THREE_IPS, send_handler)
    with pytest.raises(fetcher.HTTPException) as exc:
        await fetcher.safe_fetch("https://headinspect.ru/", max_bytes=1_000_000)

    assert exc.value.status_code == 502


async def test_http_error_status_does_not_trigger_ip_fallback(monkeypatch):
    # A 500 from the first IP is a real answer, not a connect failure - the
    # second IP must never be contacted.
    attempts = []

    def send_handler(request):
        attempts.append(_ip_of(request))
        return FakeResponse(500, headers={"content-type": "text/html"}, body=b"boom")

    _install(monkeypatch, THREE_IPS, send_handler)
    result = await fetcher.safe_fetch("https://headinspect.ru/", max_bytes=1_000_000)

    assert result.status_code == 500
    assert attempts == [THREE_IPS[0]]  # no fallback


async def test_read_timeout_after_connect_does_not_trigger_fallback(monkeypatch):
    # A ReadTimeout is raised only after the connection is established; it must
    # surface as 504 without switching to another IP.
    attempts = []

    def send_handler(request):
        attempts.append(_ip_of(request))
        raise httpx.ReadTimeout("read timed out")

    _install(monkeypatch, THREE_IPS, send_handler)
    with pytest.raises(fetcher.HTTPException) as exc:
        await fetcher.safe_fetch("https://headinspect.ru/", max_bytes=1_000_000)

    assert exc.value.status_code == 504
    assert attempts == [THREE_IPS[0]]  # no fallback on a read-phase timeout


async def test_redirect_revalidates_each_hop_and_still_supports_fallback(monkeypatch):
    # First hop redirects; the fallback machinery must not break redirects, and
    # resolve_and_validate_host must be called again for the new hop (the
    # redirect target is re-validated, exactly as before).
    resolves = []

    async def fake_resolve(url):
        resolves.append(urlsplit(url).hostname)
        return ["203.0.113.7"]

    monkeypatch.setattr(fetcher, "resolve_and_validate_host", fake_resolve)

    def send_handler(request):
        host = request.headers.get("Host")
        if host == "headinspect.ru":
            return FakeResponse(301, headers={"location": "https://www.headinspect.ru/"})
        return FakeResponse(200, headers={"content-type": "text/html"}, body=b"final")

    def factory(*a, **kw):
        return FakeAsyncClient(send_handler)

    monkeypatch.setattr(fetcher.httpx, "AsyncClient", factory)

    result = await fetcher.safe_fetch("https://headinspect.ru/", max_bytes=1_000_000)
    assert result.content == b"final"
    assert result.url == "https://www.headinspect.ru/"
    # Re-validated on each hop (no reuse of the first hop's validation):
    assert resolves == ["headinspect.ru", "www.headinspect.ru"]

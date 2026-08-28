"""Item 10: proves the global outbound-fetch semaphore introduced in
fetcher.py actually bounds real concurrency against audited sites,
independent of how many jobs/pages try to fetch at once - the
architectural prerequisite for ever safely raising MAX_CONCURRENT_AUDITS.
"""
from __future__ import annotations

import asyncio

from app import config, fetcher


class FakeResponse:
    def __init__(self, status_code=200, headers=None, body=b"x"):
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html"}
        self._body = body

    async def aiter_bytes(self):
        yield self._body

    async def aclose(self):
        pass


class FakeRequest:
    def __init__(self, method, url, headers, extensions):
        self.method, self.url, self.headers, self.extensions = method, url, headers, extensions


class ConcurrencyTrackingClient:
    def __init__(self, tracker, *a, **kw):
        self.tracker = tracker

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def build_request(self, method, url, headers=None, extensions=None):
        return FakeRequest(method, url, headers, extensions)

    async def send(self, request, stream=False):
        self.tracker["current"] += 1
        self.tracker["max_seen"] = max(self.tracker["max_seen"], self.tracker["current"])
        await asyncio.sleep(0.05)  # a slow-ish response, to make overlap likely
        self.tracker["current"] -= 1
        return FakeResponse()


async def test_global_semaphore_caps_real_concurrency_across_many_callers(monkeypatch):
    async def fake_resolve(url):
        return ["203.0.113.10"]

    monkeypatch.setattr(fetcher, "resolve_and_validate_host", fake_resolve)

    tracker = {"current": 0, "max_seen": 0}
    monkeypatch.setattr(fetcher.httpx, "AsyncClient", lambda *a, **kw: ConcurrencyTrackingClient(tracker))
    # Use a small, deterministic cap for the test rather than depending on
    # the current production value of GLOBAL_MAX_CONCURRENT_FETCHES.
    monkeypatch.setattr(fetcher, "_global_fetch_semaphore", asyncio.Semaphore(4))

    # Simulate 8 "concurrent audit jobs" each fetching a page at once - as
    # if MAX_CONCURRENT_AUDITS had been raised without this cap in place.
    await asyncio.gather(*(
        fetcher.safe_fetch(f"https://example.ru/p{i}", max_bytes=1_000_000) for i in range(8)
    ))

    assert tracker["max_seen"] <= 4, "must never exceed the process-wide cap"
    assert tracker["max_seen"] == 4, "the test should actually exercise the cap, not run serially"


def test_global_max_concurrent_fetches_equals_page_concurrency_today():
    """Today MAX_CONCURRENT_AUDITS == 1, so the global cap must equal
    PAGE_CONCURRENCY for the change to be a true no-op in production - a
    regression here would silently change current throughput/behavior."""
    assert config.GLOBAL_MAX_CONCURRENT_FETCHES == config.PAGE_CONCURRENCY

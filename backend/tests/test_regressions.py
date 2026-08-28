from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app import audit as audit_module
from app.audit import run_pages
from app.jobs import JobManager
from app.models import MetaData, PageResult


def make(url, requested_url, title=None, check_failed=False):
    r = PageResult(url=url, requested_url=requested_url, check_failed=check_failed)
    r.meta = MetaData(title=title)
    return r


def test_redirect_page_never_creates_a_false_meta_duplicate():
    """Regression guard explicitly called out in the plan: a redirect and
    its target sharing a title must not be flagged as two duplicate
    pages - the redirect record is excluded from dedup entirely."""
    results = [
        make("https://example.ru/page", "https://example.ru/old-page", title="Заголовок страницы"),
        make("https://example.ru/page", "https://example.ru/page", title="Заголовок страницы"),
        make("https://example.ru/other", "https://example.ru/other", title="Другой заголовок"),
    ]
    JobManager._apply_meta_duplicate_warnings(results)

    assert results[0].meta.warnings == []
    assert results[1].meta.warnings == []
    assert results[2].meta.warnings == []


def test_genuine_duplicate_title_across_two_distinct_pages_is_still_flagged():
    results = [
        make("https://example.ru/a", "https://example.ru/a", title="Одинаковый заголовок"),
        make("https://example.ru/b", "https://example.ru/b", title="Одинаковый заголовок"),
    ]
    JobManager._apply_meta_duplicate_warnings(results)

    assert len(results[0].meta.warnings) == 1
    assert len(results[1].meta.warnings) == 1


def test_check_failed_pages_are_excluded_from_duplicate_detection():
    results = [
        make("https://example.ru/a", "https://example.ru/a", title="T", check_failed=False),
        # In production, PageResult.url is a required, non-nullable string
        # (see models.py) - every check_failed construction site in
        # audit.py sets url to the same value as requested_url (the URL
        # that failed), never None. Mirror that here rather than passing
        # url=None, which cannot occur in real code and is rightly
        # rejected by the real Pydantic model.
        make("https://example.ru/b", "https://example.ru/b", title="T", check_failed=True),
    ]
    JobManager._apply_meta_duplicate_warnings(results)

    assert results[0].meta.warnings == []
    assert results[1].meta.warnings == []


async def test_run_pages_stop_event_halts_further_fetches(monkeypatch):
    """Item 9's interaction with audit.py: once stop_event is set, workers
    that have not started yet - or are merely queued behind
    PAGE_CONCURRENCY, not yet started - must not issue new requests.

    This exercises the *real* analyze_page (not a fake standing in for it):
    the actual production bug lived inside analyze_page's semaphore
    handling, not in run_pages' dispatch loop, and a fake analyze_page with
    no delay cannot reproduce it (every worker "wins the race" against
    detection when nothing takes real time). A small artificial delay on
    the mocked network call is what actually creates the queuing behind
    PAGE_CONCURRENCY that the real bug depended on.
    """
    fetch_calls = []

    async def fake_safe_fetch(url, **kwargs):
        fetch_calls.append(url)
        await asyncio.sleep(0.02)
        return SimpleNamespace(
            url=url, status_code=200, headers={"content-type": "text/html"}, content=b"<html></html>"
        )

    monkeypatch.setattr(audit_module, "safe_fetch", fake_safe_fetch)
    # audit.py imported PAGE_CONCURRENCY by value (`from .config import
    # PAGE_CONCURRENCY`), so the module-level name in audit_module must be
    # patched directly - patching config_module.PAGE_CONCURRENCY would not
    # affect the already-bound name run_pages actually uses.
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 4)

    stop_event = asyncio.Event()
    seen = []

    async def on_result(result):
        seen.append(result.url)
        if len(seen) == 3:
            stop_event.set()

    # A large URL list matters here: with few URLs, essentially all of them
    # get dispatched before detection could plausibly fire, so the test
    # would pass even without the fix. With 200 URLs and PAGE_CONCURRENCY=4,
    # ~196 workers are queued on the semaphore, not yet fetching, at the
    # moment stop_event fires - real production behaviour this reproduces.
    urls = [f"https://example.ru/p{i}" for i in range(200)]
    await run_pages(urls, on_result, stop_event=stop_event)

    assert len(seen) >= 3
    # Bounded well below the full URL count: a handful of requests already
    # in flight (up to PAGE_CONCURRENCY) when detection fires may still
    # complete, but the other ~196 queued workers must bail out instead of
    # each making a real request. Generous margin to avoid timing flakiness
    # while still catching a regression back to "hundreds of requests".
    assert len(fetch_calls) < 20, (
        f"expected far fewer than 200 real fetches after stop_event fired, got {len(fetch_calls)}"
    )

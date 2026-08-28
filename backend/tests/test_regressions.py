from __future__ import annotations

import asyncio

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
    that have not started yet must not issue new requests."""
    fetched = []

    async def fake_analyze_page(url, semaphore):
        fetched.append(url)
        return PageResult(url=url, requested_url=url, status_code=200)

    monkeypatch.setattr(audit_module, "analyze_page", fake_analyze_page)

    stop_event = asyncio.Event()
    seen = []

    async def on_result(result):
        seen.append(result.url)
        if len(seen) == 3:
            stop_event.set()

    urls = [f"https://example.ru/p{i}" for i in range(20)]
    await run_pages(urls, on_result, stop_event=stop_event)

    assert len(fetched) < 20
    assert len(seen) >= 3

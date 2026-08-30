"""Regression tests for the PAGE_TIMEOUT semantics fix.

Root cause (see PROPOSAL/investigation from 2026-08-29): `PAGE_TIMEOUT` used
to time the *entire* analyze_page call, including the wait for a
PAGE_CONCURRENCY semaphore slot. Since run_pages creates all worker()
coroutines via a single asyncio.gather(), they are all scheduled at
essentially the same wall-clock instant, so every URL's 30-second countdown
started at nearly the same moment regardless of when its own real work
actually began. On a large audit (leonidagutin.ru: ~500 URLs), most workers
spent all 30 seconds merely queued behind 4 concurrent slots and were
reported as "Страница не ответила за 30 с" without ever touching the
network.

The fix moves the asyncio.wait_for(...) inside analyze_page to wrap only
the real fetch-and-analyze work (audit.py::_fetch_and_analyze), applied
*after* the semaphore has actually been acquired - so PAGE_TIMEOUT now
measures a page's own processing time, never queueing time.
"""
from __future__ import annotations

import asyncio

from app import audit as audit_module
from app import jobs as jobs_module
from app.jobs import Job, JobManager
from app.models import PageResult


class FakeFetchResult:
    def __init__(self, url, status_code=200, headers=None, content=b"<html></html>"):
        self.url = url
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html"}
        self.content = content


async def test_7_1_semaphore_queue_wait_never_counts_against_page_timeout(monkeypatch):
    """Item 7.1: a URL that waits a long time for a free slot, but whose own
    processing is fast once it starts, must NOT be reported as a timeout -
    no matter how long it queued.
    """
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 1)  # fully serialized, to make queueing dramatic
    monkeypatch.setattr(audit_module, "PAGE_TIMEOUT", 0.2)

    async def fake_safe_fetch(url, **kwargs):
        await asyncio.sleep(0.03)  # fast real work, well under PAGE_TIMEOUT
        return FakeFetchResult(url=url)

    monkeypatch.setattr(audit_module, "safe_fetch", fake_safe_fetch)

    # With concurrency=1 and 10 URLs at 0.03s each, the LAST url will have
    # queued for roughly 9*0.03 =~ 0.27s before it even starts - already
    # longer than PAGE_TIMEOUT=0.2s. Under the old (buggy) semantics this
    # alone would make it (and others) time out; under the fix, only its
    # own ~0.03s of real work counts.
    urls = [f"https://example.ru/p{i}" for i in range(10)]
    seen = []

    async def on_result(result):
        seen.append(result)

    await audit_module.run_pages(urls, on_result)

    assert len(seen) == 10
    timeouts = [r for r in seen if r.check_reason == "timeout"]
    assert timeouts == [], f"expected zero spurious timeouts from queueing, got {len(timeouts)}"
    assert all(not r.check_failed for r in seen)


async def test_7_2_real_processing_over_budget_is_still_a_genuine_timeout(monkeypatch):
    """Item 7.2: once a URL has acquired its slot, its own processing time
    is still bounded by PAGE_TIMEOUT - a page that is itself slow (not
    merely queued) must still be reported as check_reason='timeout'.
    """
    monkeypatch.setattr(audit_module, "PAGE_TIMEOUT", 0.05)

    async def fake_safe_fetch(url, **kwargs):
        await asyncio.sleep(10)  # never finishes within PAGE_TIMEOUT
        return FakeFetchResult(url=url)

    monkeypatch.setattr(audit_module, "safe_fetch", fake_safe_fetch)

    seen = []

    async def on_result(result):
        seen.append(result)

    await audit_module.run_pages(["https://example.ru/slow"], on_result)

    assert len(seen) == 1
    assert seen[0].check_failed is True
    assert seen[0].check_reason == "timeout"
    assert seen[0].status_code is None
    assert seen[0].errors == []
    assert seen[0].warnings == []


async def test_7_3_large_url_list_does_not_produce_mass_false_timeouts(monkeypatch):
    """Item 7.3: direct reproduction of the leonidagutin.ru-shaped bug at
    small test scale. URL count is far larger than PAGE_CONCURRENCY, so a
    large fraction of URLs necessarily spend real time queued. With the fix,
    every URL still succeeds (each one's own work is short); this exact
    assertion fails against the pre-fix implementation (verified by hand:
    reverting analyze_page/worker to wrap the semaphore wait in
    asyncio.wait_for reproduces dozens of spurious 'timeout' results here).
    """
    monkeypatch.setattr(audit_module, "PAGE_TIMEOUT", 0.3)
    # PAGE_CONCURRENCY intentionally left at its real value (4) - this is
    # exactly the production shape, just with a much smaller PAGE_TIMEOUT
    # and per-page delay so the test runs in a fraction of a second instead
    # of tens of minutes.

    async def fake_safe_fetch(url, **kwargs):
        await asyncio.sleep(0.02)
        return FakeFetchResult(url=url)

    monkeypatch.setattr(audit_module, "safe_fetch", fake_safe_fetch)

    # 200 URLs at PAGE_CONCURRENCY=4: total real work needed if perfectly
    # pipelined is 200/4*0.02 = 1.0s, comfortably more than the 0.3s shared
    # window the *old* code would have given everyone collectively - the
    # exact shape of the original bug (500 URLs did not fit in one shared
    # 30s window either).
    urls = [f"https://example.ru/p{i}" for i in range(200)]
    seen = []

    async def on_result(result):
        seen.append(result)

    await audit_module.run_pages(urls, on_result)

    assert len(seen) == 200
    timeouts = [r for r in seen if r.check_reason == "timeout"]
    assert len(timeouts) == 0, (
        f"expected 0 spurious timeouts (each page's own work is only 0.02s), got {len(timeouts)} - "
        "this is exactly the leonidagutin.ru bug shape reproduced at test scale"
    )


async def test_7_4_audit_timeout_never_labels_unstarted_urls_as_page_timeouts(monkeypatch):
    """Item 7.4: when the *global* AUDIT_TIMEOUT fires, URLs that never got
    a chance to start (still queued behind PAGE_CONCURRENCY, or simply
    never dispatched) must not appear in results with check_reason='timeout'
    or count as check_failed - they are simply absent, and checked_urls
    honestly reflects only what was actually attempted.
    """
    monkeypatch.setattr(jobs_module, "AUDIT_TIMEOUT", 0.15)
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 2)
    monkeypatch.setattr(audit_module, "PAGE_TIMEOUT", 30.0)  # real value - AUDIT_TIMEOUT is what cuts this short

    urls = [f"https://example.ru/p{i}" for i in range(40)]

    async def fake_discover_audit_urls(url):
        return {
            "normalized_url": "https://example.ru/", "robots_url": None, "robots_found": None,
            "robots_sitemap_urls": [], "sitemap_urls": [], "sitemap_issues": [],
            "urls": urls, "limited": False, "access_blocked_status": None,
        }

    async def fake_safe_fetch(url, **kwargs):
        await asyncio.sleep(0.03)  # each page finishes quickly on its own
        return FakeFetchResult(url=url)

    monkeypatch.setattr(jobs_module, "discover_audit_urls", fake_discover_audit_urls)
    monkeypatch.setattr(audit_module, "safe_fetch", fake_safe_fetch)
    # jobs.py imports run_pages directly - use the real one so PAGE_CONCURRENCY=2
    # actually throttles this test meaningfully and AUDIT_TIMEOUT has URLs
    # left over to cut off.
    monkeypatch.setattr(jobs_module, "run_pages", audit_module.run_pages)

    manager = JobManager()
    job = Job(job_id="audit-timeout-honest-semantics", requested_url="https://example.ru/")
    await manager._run(job)

    assert job.status == "completed_partial"
    assert job.partial_reason is not None
    # Every page actually present in results genuinely completed - none of
    # them can be a "timeout" here, since each one's own work (0.03s) is
    # far under PAGE_TIMEOUT (30s); only AUDIT_TIMEOUT cut off the *rest*.
    assert all(r.check_reason != "timeout" for r in job.results)
    assert all(not r.check_failed for r in job.results)
    # Honest, non-inflated accounting: checked_urls reflects only what was
    # actually attempted, and is less than the full discovered set - the
    # remainder were never started, not "checked and failed".
    assert job.checked_urls == len(job.results)
    assert 0 < job.checked_urls < job.discovered_urls
    assert job.discovered_urls == 40


async def test_7_5_stop_event_double_check_survives_the_refactor(monkeypatch):
    """Item 7.5 / item 6: after stop_event fires, no new network request may
    start - including for workers that were still queued behind the
    semaphore when it fired. Both checks (pre-semaphore and post-semaphore)
    now live inside analyze_page itself (moved from being split between
    worker/analyze_page) - this proves both still function correctly after
    the refactor.
    """
    fetch_calls = []

    async def fake_safe_fetch(url, **kwargs):
        fetch_calls.append(url)
        await asyncio.sleep(0.02)
        return FakeFetchResult(url=url)

    monkeypatch.setattr(audit_module, "safe_fetch", fake_safe_fetch)
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 4)

    stop_event = asyncio.Event()
    seen = []

    async def on_result(result):
        seen.append(result)
        if len(seen) == 3:
            stop_event.set()

    urls = [f"https://example.ru/p{i}" for i in range(200)]
    await audit_module.run_pages(urls, on_result, stop_event=stop_event)

    assert len(fetch_calls) < 20, f"expected far fewer than 200 real fetches after stop_event fired, got {len(fetch_calls)}"
    assert len(seen) >= 3

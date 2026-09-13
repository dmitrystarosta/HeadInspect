"""Execution-model tests for the phased, work-scaled audit runner.

The old model bounded discovery+crawl by one fixed 90 s AUDIT_TIMEOUT, which
truncated healthy large sites (kaltra.nl finished ~76/299 purely because
throughput * 90 s can't reach more). The new model:

  * bounds discovery on its own (DISCOVERY_TIMEOUT);
  * gives the page crawl a deadline that scales with the number of discovered
    pages, capped by CRAWL_MAX_SECONDS (crawl_deadline_seconds);
  * keeps a per-page liveness bound (PAGE_TIMEOUT) so one hung page can't hold
    a worker;
  * adds a no-progress stall watchdog (CRAWL_STALL_TIMEOUT);
  * on any stop, cancels in-flight work cleanly (no leaked asyncio tasks) and
    keeps whatever pages were already checked as an honest partial.

These tests exercise that model end to end through JobManager._run and the real
audit.run_pages, plus the supervisor (_supervised_crawl) directly where finer
control is needed. Connect-time SSRF is covered too, including the og:image
path that now relies on safe_fetch (not a pre-fetch validate).
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import HTTPException

from app import audit as audit_module
from app import jobs as jobs_module
from app.analyzers import open_graph as og_module
from app.jobs import Job, JobManager, crawl_deadline_seconds


class FakeFetchResult:
    def __init__(self, url, status_code=200, headers=None, content=b"<html></html>"):
        self.url = url
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html"}
        self.content = content


def _discovery(urls, **overrides):
    base = {
        "normalized_url": "https://example.ru/",
        "robots_url": None,
        "robots_found": None,
        "robots_sitemap_urls": [],
        "sitemap_urls": [],
        "sitemap_issues": [],
        "urls": list(urls),
        "limited": False,
        "access_blocked_status": None,
    }
    base.update(overrides)
    return base


def _install_discovery(monkeypatch, urls, **overrides):
    async def fake_discover_audit_urls(url):
        return _discovery(urls, **overrides)

    monkeypatch.setattr(jobs_module, "discover_audit_urls", fake_discover_audit_urls)


async def _run_job():
    manager = JobManager()
    job = Job(job_id="exec-model-job", requested_url="https://example.ru/")
    await manager._run(job)
    return job


# --------------------------------------------------------------------------
# crawl_deadline_seconds: the budget that makes 500 a REAL limit, still capped
# --------------------------------------------------------------------------

def test_crawl_deadline_scales_with_pages_and_is_capped():
    from app.config import CRAWL_MAX_SECONDS

    # Monotonic in the number of pages.
    assert crawl_deadline_seconds(10) < crawl_deadline_seconds(100) < crawl_deadline_seconds(500)

    # A full 500-page crawl of a slow-but-healthy site (~2.8 s/page over
    # PAGE_CONCURRENCY=4 => ~0.7 s wall-clock/page => ~350 s) must fit inside
    # the deadline - otherwise 500 would only be a formal limit.
    assert crawl_deadline_seconds(500) >= 500 * 0.7

    # Hard ceiling holds no matter how many pages.
    assert crawl_deadline_seconds(10_000) == CRAWL_MAX_SECONDS
    assert crawl_deadline_seconds(500) <= CRAWL_MAX_SECONDS


# --------------------------------------------------------------------------
# Healthy sites complete fully (fast small, large-500, and slow-but-healthy)
# --------------------------------------------------------------------------

async def test_fast_small_site_completes_fully(monkeypatch):
    urls = [f"https://example.ru/p{i}" for i in range(8)]
    _install_discovery(monkeypatch, urls)

    async def fast_fetch(url, **kwargs):
        await asyncio.sleep(0)  # instant, healthy page
        return FakeFetchResult(url=url)

    monkeypatch.setattr(audit_module, "safe_fetch", fast_fetch)

    job = await _run_job()

    assert job.status == "completed"
    assert job.partial_reason is None
    assert job.checked_urls == 8 == job.discovered_urls
    assert all(not r.check_failed for r in job.results)


async def test_large_healthy_site_of_500_pages_completes_fully(monkeypatch):
    """The crux: 500 discovered pages that answer quickly are ALL checked -
    the limit is real, not a number the crawl can never reach. Uses the real
    default deadline (no monkeypatched timeout)."""
    urls = [f"https://example.ru/p{i}" for i in range(500)]
    _install_discovery(monkeypatch, urls)

    async def fast_fetch(url, **kwargs):
        return FakeFetchResult(url=url)

    monkeypatch.setattr(audit_module, "safe_fetch", fast_fetch)

    job = await _run_job()

    assert job.status == "completed"
    assert job.partial_reason is None
    assert job.checked_urls == 500 == job.discovered_urls
    assert len(job.results) == 500


async def test_slow_but_healthy_pages_complete_within_generous_deadline(monkeypatch):
    urls = [f"https://example.ru/p{i}" for i in range(12)]
    _install_discovery(monkeypatch, urls)

    async def slow_fetch(url, **kwargs):
        await asyncio.sleep(0.05)  # each page slow but alive
        return FakeFetchResult(url=url)

    monkeypatch.setattr(audit_module, "safe_fetch", slow_fetch)

    job = await _run_job()

    assert job.status == "completed"
    assert job.checked_urls == 12
    assert all(not r.check_failed for r in job.results)


# --------------------------------------------------------------------------
# Hung pages are bounded per-page and do not stop the crawl
# --------------------------------------------------------------------------

async def test_one_hung_page_does_not_stop_the_audit(monkeypatch):
    monkeypatch.setattr(audit_module, "PAGE_TIMEOUT", 0.2)
    urls = [f"https://example.ru/p{i}" for i in range(6)]
    _install_discovery(monkeypatch, urls)

    hung = "https://example.ru/p3"

    async def fetch(url, **kwargs):
        if url == hung:
            await asyncio.sleep(10)  # this one page hangs
        return FakeFetchResult(url=url)

    monkeypatch.setattr(audit_module, "safe_fetch", fetch)

    job = await _run_job()

    assert job.status == "completed"  # crawl finished; every page attempted
    assert job.checked_urls == 6
    timed_out = [r for r in job.results if r.check_reason == "timeout"]
    assert len(timed_out) == 1 and timed_out[0].url == hung
    assert sum(1 for r in job.results if not r.check_failed) == 5


async def test_several_hung_pages_are_each_bounded_and_crawl_still_finishes(monkeypatch):
    monkeypatch.setattr(audit_module, "PAGE_TIMEOUT", 0.2)
    urls = [f"https://example.ru/p{i}" for i in range(8)]
    _install_discovery(monkeypatch, urls)

    async def all_hang(url, **kwargs):
        await asyncio.sleep(10)
        return FakeFetchResult(url=url)

    monkeypatch.setattr(audit_module, "safe_fetch", all_hang)

    job = await _run_job()

    # Every page was attempted and turned into an honest per-page timeout; the
    # crawl completed (a hung page is a failed check, not a failed job) and no
    # worker was held longer than PAGE_TIMEOUT.
    assert job.checked_urls == 8
    assert all(r.check_reason == "timeout" and r.check_failed for r in job.results)
    assert job.status == "completed"


# --------------------------------------------------------------------------
# Audits that must be stopped: deadline (partial kept) and stall (failed / kept)
# --------------------------------------------------------------------------

async def test_deadline_stops_crawl_and_keeps_partial_results(monkeypatch):
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 2)
    monkeypatch.setattr(audit_module, "PAGE_TIMEOUT", 30.0)
    # Tiny crawl deadline; stall watchdog high so the DEADLINE is what fires.
    monkeypatch.setattr(jobs_module, "CRAWL_BASE_SECONDS", 0.25)
    monkeypatch.setattr(jobs_module, "CRAWL_PER_PAGE_SECONDS", 0.0)
    monkeypatch.setattr(jobs_module, "CRAWL_MAX_SECONDS", 0.25)
    monkeypatch.setattr(jobs_module, "CRAWL_STALL_TIMEOUT", 30.0)

    urls = [f"https://example.ru/p{i}" for i in range(40)]
    _install_discovery(monkeypatch, urls)

    async def steady_fetch(url, **kwargs):
        await asyncio.sleep(0.1)  # steady progress, but 40 * 0.1 / 2 >> 0.25 s
        return FakeFetchResult(url=url)

    monkeypatch.setattr(audit_module, "safe_fetch", steady_fetch)

    job = await _run_job()

    assert job.status == "completed_partial"
    assert job.error is None
    assert 0 < job.checked_urls < 40
    assert len(job.results) == job.checked_urls  # already-collected pages kept
    assert job.partial_reason is not None and "секунд" in job.partial_reason
    # Pages that were never started are absent, never labelled as failures.
    assert all(not r.check_failed for r in job.results)


async def test_total_stall_with_zero_results_is_a_plain_failure(monkeypatch):
    monkeypatch.setattr(audit_module, "PAGE_TIMEOUT", 30.0)  # pages don't self-complete
    # Stall watchdog fires well before the (larger) deadline.
    monkeypatch.setattr(jobs_module, "CRAWL_BASE_SECONDS", 5.0)
    monkeypatch.setattr(jobs_module, "CRAWL_PER_PAGE_SECONDS", 0.0)
    monkeypatch.setattr(jobs_module, "CRAWL_MAX_SECONDS", 5.0)
    monkeypatch.setattr(jobs_module, "CRAWL_STALL_TIMEOUT", 0.15)

    urls = [f"https://example.ru/p{i}" for i in range(8)]
    _install_discovery(monkeypatch, urls)

    async def black_hole(url, **kwargs):
        await asyncio.sleep(10)  # never returns, no progress ever recorded
        return FakeFetchResult(url=url)

    monkeypatch.setattr(audit_module, "safe_fetch", black_hole)

    job = await _run_job()

    assert job.status == "failed"
    assert job.checked_urls == 0
    assert job.error is not None and "перестал отвечать" in job.error


async def test_mass_page_timeouts_still_trigger_stall_before_deadline(monkeypatch):
    """A totally unresponsive site emits a per-page timeout result every
    ~PAGE_TIMEOUT. With the real ordering PAGE_TIMEOUT < CRAWL_STALL_TIMEOUT
    those regular timeout batches must NOT count as progress, so the stall
    watchdog still fires - well before the (much larger) crawl deadline. This
    is the defect fix: previously any PageResult refreshed last_progress, so a
    dead site kept the watchdog from ever firing and held the slot until
    CRAWL_MAX_SECONDS.

    Ratios mirror production (PAGE_TIMEOUT < CRAWL_STALL_TIMEOUT << deadline),
    scaled down for speed.
    """
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 4)
    monkeypatch.setattr(audit_module, "PAGE_TIMEOUT", 0.2)       # each attempt times out at 0.2 s
    monkeypatch.setattr(jobs_module, "CRAWL_STALL_TIMEOUT", 0.5)  # > PAGE_TIMEOUT, spans ~2 timeout batches

    async def never_responds(url, **kwargs):
        await asyncio.sleep(10)  # site never answers -> every page hits PAGE_TIMEOUT
        return FakeFetchResult(url=url)

    monkeypatch.setattr(audit_module, "safe_fetch", never_responds)

    urls = [f"https://example.ru/p{i}" for i in range(40)]
    manager = JobManager()
    job = Job(job_id="mass-timeout-stall", requested_url="https://example.ru/")
    stop_event = asyncio.Event()

    # Deadline deliberately far larger than the stall window: under the old
    # (buggy) "any result is progress" logic the periodic timeout batches would
    # keep refreshing progress and the crawl would run until ~deadline.
    deadline = 5.0
    started = time.monotonic()
    outcome = await manager._supervised_crawl(job, urls, stop_event, deadline_seconds=deadline)
    elapsed = time.monotonic() - started

    assert outcome == "stall"
    assert elapsed < deadline - 1.0, (
        f"stall should fire near the 0.5s window, not run to the {deadline}s deadline (took {elapsed:.2f}s)"
    )
    # It genuinely WAS producing per-page timeout results the whole time - the
    # exact stream that used to be mistaken for healthy progress.
    assert job.checked_urls > 0
    assert all(r.check_reason == "timeout" for r in job.results)


async def test_stall_after_some_progress_keeps_partial(monkeypatch):
    """Supervisor-level: a crawl that delivers a couple of results and then
    stops making progress is stopped as a stall, and the results already
    collected are kept."""
    monkeypatch.setattr(jobs_module, "CRAWL_STALL_TIMEOUT", 0.15)

    from app.models import PageResult

    async def fake_run_pages(urls, on_result, *, stop_event=None):
        await on_result(PageResult(url=urls[0], requested_url=urls[0], status_code=200))
        await on_result(PageResult(url=urls[1], requested_url=urls[1], status_code=200))
        await asyncio.sleep(10)  # progress stops here

    monkeypatch.setattr(jobs_module, "run_pages", fake_run_pages)

    manager = JobManager()
    job = Job(job_id="stall-partial", requested_url="https://example.ru/")
    stop_event = asyncio.Event()
    outcome = await manager._supervised_crawl(
        job, ["https://example.ru/a", "https://example.ru/b", "https://example.ru/c"],
        stop_event, deadline_seconds=5.0,
    )

    assert outcome == "stall"
    assert job.checked_urls == 2
    assert len(job.results) == 2


# --------------------------------------------------------------------------
# No leaked asyncio tasks after a forced stop
# --------------------------------------------------------------------------

async def test_no_pending_tasks_leak_after_deadline_cancels_the_crawl(monkeypatch):
    monkeypatch.setattr(audit_module, "PAGE_TIMEOUT", 30.0)  # pages must be cancelled, not self-finish
    monkeypatch.setattr(jobs_module, "CRAWL_BASE_SECONDS", 0.15)
    monkeypatch.setattr(jobs_module, "CRAWL_PER_PAGE_SECONDS", 0.0)
    monkeypatch.setattr(jobs_module, "CRAWL_MAX_SECONDS", 0.15)
    monkeypatch.setattr(jobs_module, "CRAWL_STALL_TIMEOUT", 30.0)

    urls = [f"https://example.ru/p{i}" for i in range(12)]
    _install_discovery(monkeypatch, urls)

    async def hang(url, **kwargs):
        await asyncio.sleep(10)
        return FakeFetchResult(url=url)

    monkeypatch.setattr(audit_module, "safe_fetch", hang)

    current = asyncio.current_task()
    before = {t for t in asyncio.all_tasks() if t is not current}

    manager = JobManager()
    job = Job(job_id="leak-check", requested_url="https://example.ru/")
    await manager._run(job)

    # Let any cancellations settle.
    for _ in range(5):
        await asyncio.sleep(0)

    leaked = {t for t in asyncio.all_tasks() if t is not current and not t.done()} - before
    assert not leaked, f"leaked {len(leaked)} pending task(s) after the crawl was stopped"
    assert job.status == "completed_partial" or job.status == "failed"


# --------------------------------------------------------------------------
# Concurrency bound is honoured
# --------------------------------------------------------------------------

async def test_page_concurrency_bound_is_never_exceeded(monkeypatch):
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 3)
    urls = [f"https://example.ru/p{i}" for i in range(15)]
    _install_discovery(monkeypatch, urls)

    state = {"in_flight": 0, "peak": 0}

    async def counting_fetch(url, **kwargs):
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        try:
            await asyncio.sleep(0.02)
            return FakeFetchResult(url=url)
        finally:
            state["in_flight"] -= 1

    monkeypatch.setattr(audit_module, "safe_fetch", counting_fetch)

    job = await _run_job()

    assert job.status == "completed"
    assert job.checked_urls == 15
    assert state["peak"] <= 3, f"peak concurrency {state['peak']} exceeded the bound of 3"
    assert state["peak"] >= 2, "expected real concurrency, not fully serialized"


# --------------------------------------------------------------------------
# SSRF invariants at the actual fetch (pages and og:image)
# --------------------------------------------------------------------------

async def test_forbidden_page_is_check_failed_and_crawl_survives(monkeypatch):
    urls = ["https://example.ru/ok", "https://internal.example/secret"]
    _install_discovery(monkeypatch, urls)

    async def fetch(url, **kwargs):
        if "internal.example" in url:
            # Exactly what safe_fetch raises for a host resolving to a
            # forbidden IP - discovery no longer pre-filters such a URL, so it
            # reaches the fetch and must be refused here, as a normal error.
            raise HTTPException(status_code=400, detail="Hostname resolves to a forbidden IP address")
        return FakeFetchResult(url=url)

    monkeypatch.setattr(audit_module, "safe_fetch", fetch)

    job = await _run_job()

    assert job.status == "completed"
    assert job.checked_urls == 2
    failed = [r for r in job.results if r.check_failed]
    assert len(failed) == 1 and failed[0].check_reason == "network"


async def test_og_image_ssrf_is_rejected_by_safe_fetch(monkeypatch):
    """After removing the pre-fetch validate_public_url, an og:image that
    resolves to a forbidden address is caught by safe_fetch and reported as
    inaccessible - never connected to, never a crash."""
    async def rejecting_fetch(url, **kwargs):
        raise HTTPException(status_code=400, detail="Hostname resolves to a forbidden IP address")

    monkeypatch.setattr(og_module, "safe_fetch", rejecting_fetch)

    og = {"og:title": ["T"], "og:image": ["https://cdn.internal/secret.jpg"]}
    data, errors, warnings = await og_module.analyze_open_graph(og, "https://example.ru/page")

    assert data.image_accessible is False
    assert any("недоступен" in e for e in errors)


async def test_og_image_syntactic_guard_still_rejects_before_fetch(monkeypatch):
    """The syntactic guard (normalize_public_url) is retained: a structurally
    invalid og:image (embedded credentials) is rejected up front, and
    safe_fetch is never called for it."""
    called = {"fetch": False}

    async def should_not_be_called(url, **kwargs):
        called["fetch"] = True
        return FakeFetchResult(url=url)

    monkeypatch.setattr(og_module, "safe_fetch", should_not_be_called)

    og = {"og:title": ["T"], "og:image": ["https://user:pass@cdn.example.ru/x.jpg"]}
    data, errors, warnings = await og_module.analyze_open_graph(og, "https://example.ru/page")

    assert data.image_accessible is False
    assert any("Некорректный og:image" in e for e in errors)
    assert called["fetch"] is False

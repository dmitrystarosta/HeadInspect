"""Tests for the concurrent-audit execution model.

Model under test:
  * GLOBAL_MAX_CONCURRENT_FETCHES - one process-wide cap on outbound requests,
    the real resource limit, enforced in fetcher._global_fetch_semaphore.
  * PAGE_CONCURRENCY - each audit's worker-pool size (audit.run_pages), so a
    single audit issues at most this many concurrent requests and can never
    hold more than this many of the global permits -> structural fairness.
  * MAX_CONCURRENT_AUDITS - how many audits run before new ones queue.

Two harnesses are used:
  - "scheduler level": monkeypatch audit.safe_fetch to observe per-audit
    concurrency and prove several audits' _run coroutines progress in parallel
    (one audit's slow/timeout pages never block another's).
  - "real fetcher level": drive the crawl through the real fetcher with a fake
    httpx client + a small global semaphore, to prove the GLOBAL cap holds
    across audits and a small audit finishes while a big one is still running,
    all through the one shared pool.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from urllib.parse import urlsplit

import pytest

from app import audit as audit_module
from app import fetcher as fetcher_module
from app import jobs as jobs_module
from app.jobs import Job, JobManager


# --------------------------------------------------------------------------
# Scheduler-level harness (monkeypatch audit.safe_fetch)
# --------------------------------------------------------------------------

class FakeFetchResult:
    def __init__(self, url, status_code=200, headers=None, content=b"<html></html>"):
        self.url = url
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html"}
        self.content = content


class Tracker:
    def __init__(self):
        self.g_cur = 0
        self.g_peak = 0
        self.a_cur = defaultdict(int)
        self.a_peak = defaultdict(int)
        self.start_order = []


def _aid(url):
    return urlsplit(url).hostname


def make_tracking_fetch(tracker, *, delay=0.01, hang_hosts=()):
    async def fake_safe_fetch(url, **kwargs):
        aid = _aid(url)
        tracker.g_cur += 1
        tracker.g_peak = max(tracker.g_peak, tracker.g_cur)
        tracker.a_cur[aid] += 1
        tracker.a_peak[aid] = max(tracker.a_peak[aid], tracker.a_cur[aid])
        tracker.start_order.append(aid)
        try:
            if aid in hang_hosts:
                await asyncio.sleep(100)  # never returns -> hits PAGE_TIMEOUT
            await asyncio.sleep(delay)
            return FakeFetchResult(url=url)
        finally:
            tracker.g_cur -= 1
            tracker.a_cur[aid] -= 1
    return fake_safe_fetch


def install_discovery(monkeypatch, page_counts):
    async def fake_discover(url):
        host = urlsplit(url).hostname
        n = page_counts.get(host, 0)
        base = url.rstrip("/")
        return {
            "normalized_url": url, "robots_url": None, "robots_found": None,
            "robots_sitemap_urls": [], "sitemap_urls": [], "sitemap_issues": [],
            "urls": [f"{base}/p{i}" for i in range(n)],
            "limited": False, "access_blocked_status": None,
        }
    monkeypatch.setattr(jobs_module, "discover_audit_urls", fake_discover)


def make_job(host):
    return Job(job_id=f"job-{host}", requested_url=f"https://{host}/", client_ip="10.0.0.1")


async def test_two_audits_progress_concurrently_and_per_audit_cap_holds(monkeypatch):
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 4)
    tracker = Tracker()
    monkeypatch.setattr(audit_module, "safe_fetch", make_tracking_fetch(tracker, delay=0.01))
    install_discovery(monkeypatch, {"big1.example.ru": 30, "big2.example.ru": 30})

    manager = JobManager()
    a, b = make_job("big1.example.ru"), make_job("big2.example.ru")
    await asyncio.gather(manager._run(a), manager._run(b))

    assert a.status == "completed" and b.status == "completed"
    assert a.checked_urls == 30 and b.checked_urls == 30
    # Per-audit cap never exceeded.
    assert tracker.a_peak["big1.example.ru"] <= 4
    assert tracker.a_peak["big2.example.ru"] <= 4
    # They genuinely interleaved (both appear in the first handful of fetches),
    # i.e. one did not run to completion before the other started.
    early = tracker.start_order[:16]
    assert "big1.example.ru" in early and "big2.example.ru" in early


async def test_five_audits_all_make_progress_no_starvation(monkeypatch):
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 4)
    tracker = Tracker()
    monkeypatch.setattr(audit_module, "safe_fetch", make_tracking_fetch(tracker, delay=0.01))
    hosts = [f"s{i}.example.ru" for i in range(5)]
    install_discovery(monkeypatch, {h: 15 for h in hosts})

    manager = JobManager()
    jobs = [make_job(h) for h in hosts]
    await asyncio.gather(*(manager._run(j) for j in jobs))

    assert all(j.status == "completed" for j in jobs)
    assert all(j.checked_urls == 15 for j in jobs)
    # Every audit started fetching early - none was starved while others ran.
    assert set(hosts) <= set(tracker.start_order[:20])


async def test_slow_pages_in_one_audit_do_not_stall_another(monkeypatch):
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 4)
    tracker = Tracker()
    fetch = make_tracking_fetch(tracker, delay=0.01)

    async def mixed_fetch(url, **kwargs):
        # The "slow" audit's pages take much longer than the "fast" audit's.
        if _aid(url) == "slow.example.ru":
            await asyncio.sleep(0.15)
            return FakeFetchResult(url=url)
        return await fetch(url, **kwargs)

    monkeypatch.setattr(audit_module, "safe_fetch", mixed_fetch)
    install_discovery(monkeypatch, {"slow.example.ru": 40, "fast.example.ru": 8})

    manager = JobManager()
    slow, fast = make_job("slow.example.ru"), make_job("fast.example.ru")
    slow_task = asyncio.create_task(manager._run(slow))
    await asyncio.sleep(0.02)  # let the slow audit occupy its workers

    await manager._run(fast)
    assert fast.status == "completed" and fast.checked_urls == 8
    # The fast audit finished while the slow one is still going.
    assert slow.status in ("discovering", "running")

    slow_task.cancel()
    try:
        await slow_task
    except asyncio.CancelledError:
        pass


async def test_page_timeouts_in_one_audit_do_not_block_another(monkeypatch):
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 4)
    monkeypatch.setattr(audit_module, "PAGE_TIMEOUT", 0.1)
    tracker = Tracker()
    monkeypatch.setattr(audit_module, "safe_fetch", make_tracking_fetch(tracker, delay=0.01, hang_hosts={"hang.example.ru"}))
    install_discovery(monkeypatch, {"hang.example.ru": 8, "fast.example.ru": 8})

    manager = JobManager()
    hang, fast = make_job("hang.example.ru"), make_job("fast.example.ru")
    hang_task = asyncio.create_task(manager._run(hang))
    await asyncio.sleep(0.02)

    started = time.monotonic()
    await manager._run(fast)
    elapsed = time.monotonic() - started

    assert fast.status == "completed" and fast.checked_urls == 8
    # The fast audit did not have to wait on the other audit's per-page
    # timeouts - it finished in well under a single PAGE_TIMEOUT cycle.
    assert elapsed < 0.1, f"fast audit was blocked by the hanging one (took {elapsed:.3f}s)"

    hang_task.cancel()
    try:
        await hang_task
    except asyncio.CancelledError:
        pass


async def test_cancelled_audit_releases_its_slot_and_in_flight_fetches(monkeypatch):
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 4)
    tracker = Tracker()
    monkeypatch.setattr(audit_module, "safe_fetch", make_tracking_fetch(tracker, delay=100))  # never completes
    install_discovery(monkeypatch, {"cancelme.example.ru": 20, "after.example.ru": 5})

    manager = JobManager()
    manager.audit_slots = asyncio.Semaphore(1)  # single slot, so release is observable

    victim = make_job("cancelme.example.ru")
    victim_task = asyncio.create_task(manager._run(victim))
    await asyncio.sleep(0.05)
    assert tracker.a_cur["cancelme.example.ru"] == 4  # its pool is fully engaged, holding the slot

    victim_task.cancel()
    try:
        await victim_task
    except asyncio.CancelledError:
        pass
    for _ in range(5):
        await asyncio.sleep(0)
    # Its in-flight fetches are gone and the slot is free again.
    assert tracker.a_cur["cancelme.example.ru"] == 0

    # A normal fetch for the next audit; it must now be able to acquire the slot.
    monkeypatch.setattr(audit_module, "safe_fetch", make_tracking_fetch(tracker, delay=0.01))
    after = make_job("after.example.ru")
    await asyncio.wait_for(manager._run(after), timeout=2.0)
    assert after.status == "completed" and after.checked_urls == 5


async def test_no_task_leak_across_concurrent_audits(monkeypatch):
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 4)
    tracker = Tracker()
    monkeypatch.setattr(audit_module, "safe_fetch", make_tracking_fetch(tracker, delay=0.01))
    hosts = [f"n{i}.example.ru" for i in range(4)]
    install_discovery(monkeypatch, {h: 12 for h in hosts})

    current = asyncio.current_task()
    before = {t for t in asyncio.all_tasks() if t is not current}

    manager = JobManager()
    await asyncio.gather(*(manager._run(make_job(h)) for h in hosts))
    for _ in range(5):
        await asyncio.sleep(0)

    leaked = {t for t in asyncio.all_tasks() if t is not current and not t.done()} - before
    assert not leaked, f"{len(leaked)} leaked task(s) after concurrent audits"


async def test_burst_beyond_old_cap_all_run_concurrently_none_queued(monkeypatch):
    # A burst of 10 users arriving together - more than the old MAX_CONCURRENT_AUDITS
    # of 6 - must ALL start immediately and share the global fetch pool, not queue.
    # (Real load stays bounded by GLOBAL_MAX_CONCURRENT_FETCHES regardless.)
    from app.config import MAX_CONCURRENT_AUDITS
    assert MAX_CONCURRENT_AUDITS >= 10  # the whole point of this change

    gate = asyncio.Event()

    async def gated_discover(url):
        await gate.wait()  # each audit parks in discovery, holding its slot
        return {
            "normalized_url": url, "robots_url": None, "robots_found": None,
            "robots_sitemap_urls": [], "sitemap_urls": [], "sitemap_issues": [],
            "urls": [], "limited": False, "access_blocked_status": None,
        }

    monkeypatch.setattr(jobs_module, "discover_audit_urls", gated_discover)

    manager = JobManager()  # real default audit_slots
    jobs = [Job(job_id=f"burst-{i}", requested_url=f"https://b{i}.example.ru/", client_ip=f"9.0.0.{i}") for i in range(10)]
    tasks = [asyncio.create_task(manager._run(j)) for j in jobs]
    await asyncio.sleep(0.05)

    assert all(j.status == "discovering" for j in jobs), \
        f"some audits queued instead of starting: {[j.status for j in jobs]}"
    assert not any(j.status == "queued" for j in jobs)

    gate.set()
    for t in tasks:
        await asyncio.wait_for(t, timeout=2.0)
    assert all(j.status == "completed" for j in jobs)


async def test_queue_engages_only_at_the_concurrent_audit_limit(monkeypatch):
    # A hard limit of 2 concurrent audits: the 1st and 2nd run immediately
    # (neither queues); only the 3rd queues.
    gate = asyncio.Event()

    async def gated_discover(url):
        await gate.wait()  # every audit parks inside discovery, holding its slot
        return {
            "normalized_url": url, "robots_url": None, "robots_found": None,
            "robots_sitemap_urls": [], "sitemap_urls": [], "sitemap_issues": [],
            "urls": [], "limited": False, "access_blocked_status": None,
        }

    monkeypatch.setattr(jobs_module, "discover_audit_urls", gated_discover)

    manager = JobManager()
    manager.audit_slots = asyncio.Semaphore(2)

    a = Job(job_id="A", requested_url="https://a.example.ru/", client_ip="1.1.1.1")
    b = Job(job_id="B", requested_url="https://b.example.ru/", client_ip="2.2.2.2")
    c = Job(job_id="C", requested_url="https://c.example.ru/", client_ip="3.3.3.3")
    ta = asyncio.create_task(manager._run(a))
    tb = asyncio.create_task(manager._run(b))
    tc = asyncio.create_task(manager._run(c))
    await asyncio.sleep(0.05)

    # Below the limit nobody queues; the third audit does.
    assert a.status == "discovering"
    assert b.status == "discovering"
    assert c.status == "queued"

    gate.set()
    for t in (ta, tb, tc):
        await asyncio.wait_for(t, timeout=2.0)
    assert c.status == "completed"


# --------------------------------------------------------------------------
# Real-fetcher harness (exercises the actual global semaphore across audits)
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self):
        self.status_code = 200
        self.headers = {"content-type": "text/html"}

    async def aiter_bytes(self):
        yield b"<html><title>t</title></html>"

    async def aclose(self):
        pass


class _FakeRequest:
    def __init__(self, headers, extensions):
        self.headers = headers or {}
        self.extensions = extensions or {}


def _make_real_fetch_client(tracker, delay):
    class Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def build_request(self, method, url, headers=None, extensions=None):
            return _FakeRequest(headers, extensions)

        async def send(self, request, stream=False):
            host = (request.headers or {}).get("Host", "?")
            tracker.g_cur += 1
            tracker.g_peak = max(tracker.g_peak, tracker.g_cur)
            tracker.start_order.append(host)
            try:
                await asyncio.sleep(delay)
                return _FakeResponse()
            finally:
                tracker.g_cur -= 1
    return Client


def _install_real_fetcher(monkeypatch, tracker, *, global_cap, delay):
    async def fake_resolve(host):
        return ["203.0.113.10"]
    monkeypatch.setattr(fetcher_module, "resolve_and_validate_host", fake_resolve)
    monkeypatch.setattr(fetcher_module.httpx, "AsyncClient", _make_real_fetch_client(tracker, delay))
    monkeypatch.setattr(fetcher_module, "_global_fetch_semaphore", asyncio.Semaphore(global_cap))


async def test_global_fetch_cap_never_exceeded_across_concurrent_audits(monkeypatch):
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 4)
    tracker = Tracker()
    _install_real_fetcher(monkeypatch, tracker, global_cap=6, delay=0.02)
    install_discovery(monkeypatch, {f"g{i}.example.ru": 20 for i in range(3)})

    manager = JobManager()
    jobs = [make_job(f"g{i}.example.ru") for i in range(3)]
    await asyncio.gather(*(manager._run(j) for j in jobs))

    assert all(j.status == "completed" for j in jobs)
    assert all(j.checked_urls == 20 for j in jobs)
    # 3 audits * 4 workers = 12 would-be concurrent fetches, but the shared
    # global pool holds the real number to its cap - and actually reaches it.
    assert tracker.g_peak <= 6, f"global cap exceeded: {tracker.g_peak}"
    assert tracker.g_peak == 6


async def test_small_audit_finishes_before_big_one_through_shared_pool(monkeypatch):
    monkeypatch.setattr(audit_module, "PAGE_CONCURRENCY", 4)
    tracker = Tracker()
    _install_real_fetcher(monkeypatch, tracker, global_cap=6, delay=0.02)
    install_discovery(monkeypatch, {"big.example.ru": 60, "small.example.ru": 4})

    manager = JobManager()
    big, small = make_job("big.example.ru"), make_job("small.example.ru")
    big_task = asyncio.create_task(manager._run(big))
    await asyncio.sleep(0.03)  # let the big audit saturate its 4 of the 6 slots

    await manager._run(small)
    # The small audit got its share of the shared pool and completed while the
    # big one is still running - it was NOT stuck behind the big audit.
    assert small.status == "completed" and small.checked_urls == 4
    assert big.status in ("discovering", "running")
    assert tracker.g_peak <= 6

    big_task.cancel()
    try:
        await big_task
    except asyncio.CancelledError:
        pass

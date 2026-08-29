"""Regression tests for job-state isolation, added after a real production
incident: after job A (zipkran.ru) finished completed_partial with a
mid-audit block, a *new* job B (bionicashow.ru) still showed A's
partial-completion banner and failed-page counters on the frontend.

The frontend-side fix (never leaving stale DOM from a previous job) lives in
tests/frontend/job-isolation.test.mjs. This file proves the backend half:
JobManager keeps every job's state completely separate - a new Job object
gets none of a previous job's counters, flags, or messages, and finishing
job B never mutates job A's already-stored results.
"""
from __future__ import annotations

import asyncio

from app import jobs as jobs_module
from app.jobs import Job, JobManager
from app.models import PageResult


async def test_sequential_jobs_do_not_share_any_state(monkeypatch):
    monkeypatch.setattr(jobs_module, "AUDIT_TIMEOUT", 30)
    manager = JobManager()

    # --- Job A: zipkran.ru-shaped run - blocked mid-audit, completed_partial,
    # with real counters/flags/messages populated. ---------------------------
    urls_a = [f"https://zipkran.ru/p{i}" for i in range(50)]

    async def discover_a(url):
        return {
            "normalized_url": "https://zipkran.ru/",
            "robots_url": "https://zipkran.ru/robots.txt",
            "robots_found": True,
            "robots_sitemap_urls": ["https://zipkran.ru/sitemap.xml"],
            "sitemap_urls": ["https://zipkran.ru/sitemap.xml"],
            "sitemap_issues": [],
            "urls": urls_a,
            "limited": False,
            "access_blocked_status": None,
        }

    async def run_pages_a(urls, on_result, *, stop_event=None):
        for i, u in enumerate(urls[:30]):
            await on_result(PageResult(url=u, requested_url=u, status_code=200))
        for i, u in enumerate(urls[30:42]):
            # Mirrors the real analyze_page (app/audit.py): a 401/403/429
            # response is check_failed=True/check_reason="access_blocked",
            # not a content-analysis error - see PROPOSAL_2026-08-29.
            await on_result(PageResult(
                url=u, requested_url=u, status_code=403,
                check_failed=True, check_reason="access_blocked",
            ))

    monkeypatch.setattr(jobs_module, "discover_audit_urls", discover_a)
    monkeypatch.setattr(jobs_module, "run_pages", run_pages_a)

    job_a = Job(job_id="job-a-zipkran", requested_url="https://zipkran.ru/")
    manager.jobs[job_a.job_id] = job_a
    await manager._run(job_a)

    assert job_a.status == "completed_partial"
    assert job_a.blocked_mid_audit is True
    assert job_a.mid_audit_block_status == 403
    assert job_a.access_blocked_status is None
    assert job_a.partial_reason is not None
    assert job_a.checked_urls > 0
    assert job_a.failed_checks > 0
    assert len(job_a.results) > 0
    job_a_snapshot = {
        "checked_urls": job_a.checked_urls,
        "failed_checks": job_a.failed_checks,
        "discovered_urls": job_a.discovered_urls,
        "errors_found": job_a.errors_found,
        "partial_reason": job_a.partial_reason,
        "results_count": len(job_a.results),
    }

    # --- Job B: bionicashow.ru-shaped run - completes normally, no
    # blocking, different counts entirely. ------------------------------------
    urls_b = [f"https://bionicashow.ru/p{i}" for i in range(10)]

    async def discover_b(url):
        return {
            "normalized_url": "https://bionicashow.ru/",
            "robots_url": "https://bionicashow.ru/robots.txt",
            "robots_found": True,
            "robots_sitemap_urls": ["https://bionicashow.ru/sitemap.xml"],
            "sitemap_urls": ["https://bionicashow.ru/sitemap.xml"],
            "sitemap_issues": [],
            "urls": urls_b,
            "limited": False,
            "access_blocked_status": None,
        }

    async def run_pages_b(urls, on_result, *, stop_event=None):
        for u in urls:
            await on_result(PageResult(url=u, requested_url=u, status_code=200))

    monkeypatch.setattr(jobs_module, "discover_audit_urls", discover_b)
    monkeypatch.setattr(jobs_module, "run_pages", run_pages_b)

    job_b = Job(job_id="job-b-bionicashow", requested_url="https://bionicashow.ru/")
    manager.jobs[job_b.job_id] = job_b
    await manager._run(job_b)

    # Job B must be a completely clean result, with none of A's state.
    assert job_b.status == "completed"
    assert job_b.blocked_mid_audit is False
    assert job_b.mid_audit_block_status is None
    assert job_b.partial_reason is None
    assert job_b.checked_urls == 10
    assert job_b.failed_checks == 0
    assert len(job_b.results) == 10
    assert all(r.url.startswith("https://bionicashow.ru/") for r in job_b.results)

    # Explicitly assert none of A's numbers leaked into B.
    assert job_b.checked_urls != job_a_snapshot["checked_urls"]
    assert job_b.failed_checks != job_a_snapshot["failed_checks"]
    assert job_b.partial_reason != job_a_snapshot["partial_reason"]

    # And running/finishing B must not have mutated A's already-stored
    # results - the two Job objects must remain fully independent.
    assert job_a.checked_urls == job_a_snapshot["checked_urls"]
    assert job_a.failed_checks == job_a_snapshot["failed_checks"]
    assert job_a.partial_reason == job_a_snapshot["partial_reason"]
    assert len(job_a.results) == job_a_snapshot["results_count"]
    assert all(r.url.startswith("https://zipkran.ru/") for r in job_a.results)

    # Response models built for each job_id must reflect only that job.
    status_a = manager.status_model(job_a)
    status_b = manager.status_model(job_b)
    assert status_a.job_id == "job-a-zipkran"
    assert status_b.job_id == "job-b-bionicashow"
    assert status_a.blocked_mid_audit is True
    assert status_b.blocked_mid_audit is False
    assert status_a.partial_reason != status_b.partial_reason

    results_a = manager.results_model(job_a)
    results_b = manager.results_model(job_b)
    assert results_a.checked_urls != results_b.checked_urls
    assert all(r.url.startswith("https://zipkran.ru/") for r in results_a.results)
    assert all(r.url.startswith("https://bionicashow.ru/") for r in results_b.results)


def test_job_dataclass_has_no_shared_mutable_defaults():
    """Every list/dict field on Job must use its own fresh instance per job
    (dataclasses.field(default_factory=...)), never a single shared mutable
    object accidentally reused across instances - a classic Python trap
    that would silently leak state between jobs even without any block-
    detection or partial-results logic involved at all."""
    job_a = Job(job_id="a", requested_url="https://a.example/")
    job_b = Job(job_id="b", requested_url="https://b.example/")

    assert job_a.results is not job_b.results
    assert job_a.robots_sitemap_urls is not job_b.robots_sitemap_urls
    assert job_a.sitemap_urls is not job_b.sitemap_urls
    assert job_a.sitemap_issues is not job_b.sitemap_issues
    assert job_a.lock is not job_b.lock

    job_a.results.append(PageResult(url="https://a.example/", requested_url="https://a.example/"))
    job_a.sitemap_issues.append("some issue")
    assert job_b.results == []
    assert job_b.sitemap_issues == []


async def test_job_manager_jobs_dict_keeps_jobs_fully_separate():
    """The JobManager.jobs dict itself must not let one job's mutation
    affect another's stored Job object - sanity check on the storage layer
    the two tests above build on."""
    manager = JobManager()
    job_a = Job(job_id="a", requested_url="https://a.example/", checked_urls=5, failed_checks=2)
    job_b = Job(job_id="b", requested_url="https://b.example/", checked_urls=9, failed_checks=0)
    manager.jobs[job_a.job_id] = job_a
    manager.jobs[job_b.job_id] = job_b

    manager.get("a").checked_urls = 999
    assert manager.get("b").checked_urls == 9


async def test_check_reason_does_not_leak_between_sequential_jobs(monkeypatch):
    """Item 7 of the updated task: job A mixes access_blocked (zipkran.ru-
    shaped) and timeout (radov39.ru-shaped) pages; job B is a clean run
    straight afterwards. Job B's results must carry none of job A's
    check_reason values, and job A's stored results must be untouched by
    running job B.
    """
    monkeypatch.setattr(jobs_module, "AUDIT_TIMEOUT", 30)
    manager = JobManager()

    urls_a = [f"https://zipkran.ru/p{i}" for i in range(10)]

    async def discover_a(url):
        return {
            "normalized_url": "https://zipkran.ru/", "robots_url": None, "robots_found": None,
            "robots_sitemap_urls": [], "sitemap_urls": [], "sitemap_issues": [],
            "urls": urls_a, "limited": False, "access_blocked_status": None,
        }

    async def run_pages_a(urls, on_result, *, stop_event=None):
        for i, u in enumerate(urls):
            if i < 5:
                await on_result(PageResult(url=u, requested_url=u, status_code=200))
            elif i < 8:
                await on_result(PageResult(
                    url=u, requested_url=u, status_code=403,
                    check_failed=True, check_reason="access_blocked",
                ))
            else:
                await on_result(PageResult(
                    url=u, requested_url=u, status_code=None,
                    check_failed=True, check_reason="timeout",
                ))

    monkeypatch.setattr(jobs_module, "discover_audit_urls", discover_a)
    monkeypatch.setattr(jobs_module, "run_pages", run_pages_a)

    job_a = Job(job_id="job-a-mixed-reasons", requested_url="https://zipkran.ru/")
    manager.jobs[job_a.job_id] = job_a
    await manager._run(job_a)

    reasons_in_a = {r.check_reason for r in job_a.results}
    assert reasons_in_a == {None, "access_blocked", "timeout"}
    job_a_results_snapshot = [(r.url, r.check_reason) for r in job_a.results]

    # --- Job B: clean run, no failures of any kind. -------------------------
    urls_b = [f"https://bionicashow.ru/p{i}" for i in range(6)]

    async def discover_b(url):
        return {
            "normalized_url": "https://bionicashow.ru/", "robots_url": None, "robots_found": None,
            "robots_sitemap_urls": [], "sitemap_urls": [], "sitemap_issues": [],
            "urls": urls_b, "limited": False, "access_blocked_status": None,
        }

    async def run_pages_b(urls, on_result, *, stop_event=None):
        for u in urls:
            await on_result(PageResult(url=u, requested_url=u, status_code=200))

    monkeypatch.setattr(jobs_module, "discover_audit_urls", discover_b)
    monkeypatch.setattr(jobs_module, "run_pages", run_pages_b)

    job_b = Job(job_id="job-b-clean", requested_url="https://bionicashow.ru/")
    manager.jobs[job_b.job_id] = job_b
    await manager._run(job_b)

    # Job B has none of job A's reasons.
    reasons_in_b = {r.check_reason for r in job_b.results}
    assert reasons_in_b == {None}
    assert job_b.failed_checks == 0

    # Job A's stored results are byte-for-byte the same after job B ran.
    assert [(r.url, r.check_reason) for r in job_a.results] == job_a_results_snapshot

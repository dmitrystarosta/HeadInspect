from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app import jobs as jobs_module
from app.jobs import Job, JobManager
from app.models import PageResult


def test_get_missing_job_raises_404_with_stable_contract():
    """Item 7's backend half: the API contract stays a clean 404 with a
    `detail` string - the frontend is responsible for turning this into the
    Russian "results no longer available" screen (see
    tests/frontend/open-existing-job.test.mjs), not the backend.
    """
    manager = JobManager()
    with pytest.raises(HTTPException) as exc_info:
        manager.get("does-not-exist")
    assert exc_info.value.status_code == 404


async def test_audit_timeout_with_partial_results_becomes_completed_partial(monkeypatch):
    """Item 8: if AUDIT_TIMEOUT fires but some pages were already checked,
    the job must not be reported as a bare failure - it must carry the
    partial results plus a clear, non-alarming reason.
    """
    monkeypatch.setattr(jobs_module, "AUDIT_TIMEOUT", 0.05)

    async def fake_discover_audit_urls(url):
        return {
            "normalized_url": "https://example.ru/",
            "robots_url": None,
            "robots_found": None,
            "robots_sitemap_urls": [],
            "sitemap_urls": [],
            "sitemap_issues": [],
            "urls": ["https://example.ru/a", "https://example.ru/b", "https://example.ru/c"],
            "limited": False,
            "access_blocked_status": None,
        }

    async def fake_run_pages(urls, on_result, *, stop_event=None):
        # Simulate checking a couple of pages quickly, then hanging well
        # past AUDIT_TIMEOUT (as a genuinely slow/unresponsive site would).
        await on_result(PageResult(url=urls[0], requested_url=urls[0], status_code=200))
        await on_result(PageResult(url=urls[1], requested_url=urls[1], status_code=200))
        await asyncio.sleep(10)

    monkeypatch.setattr(jobs_module, "discover_audit_urls", fake_discover_audit_urls)
    monkeypatch.setattr(jobs_module, "run_pages", fake_run_pages)

    manager = JobManager()
    job = Job(job_id="timeout-job", requested_url="https://example.ru/")
    await manager._run(job)

    assert job.status == "completed_partial"
    assert job.checked_urls == 2
    assert job.partial_reason is not None
    assert "секунд" in job.partial_reason  # explains it was a time limit, in Russian
    assert job.error is None  # `error` stays reserved for genuine failures


async def test_audit_timeout_with_zero_checked_pages_stays_a_plain_failure(monkeypatch):
    """The other half of item 8: if not even one page was checked before the
    timeout, a plain failure is still the right, honest outcome.
    """
    monkeypatch.setattr(jobs_module, "AUDIT_TIMEOUT", 0.05)

    async def fake_discover_audit_urls(url):
        await asyncio.sleep(10)  # discovery itself hangs
        raise AssertionError("unreachable")

    monkeypatch.setattr(jobs_module, "discover_audit_urls", fake_discover_audit_urls)

    manager = JobManager()
    job = Job(job_id="timeout-job-2", requested_url="https://example.ru/")
    await manager._run(job)

    assert job.status == "failed"
    assert job.checked_urls == 0
    assert job.error is not None


async def test_single_blocked_page_does_not_stop_the_whole_audit(monkeypatch):
    """Test 11 from the plan: one 401/403/429 among many healthy pages must
    be reported as a normal per-page error, not treated as a site-wide
    block (see tests/test_jobs_block_detection... covered here at the
    JobManager._run level for completeness).
    """
    monkeypatch.setattr(jobs_module, "AUDIT_TIMEOUT", 30)

    urls = [f"https://example.ru/p{i}" for i in range(10)]

    async def fake_discover_audit_urls(url):
        return {
            "normalized_url": "https://example.ru/",
            "robots_url": None,
            "robots_found": None,
            "robots_sitemap_urls": [],
            "sitemap_urls": [],
            "sitemap_issues": [],
            "urls": urls,
            "limited": False,
            "access_blocked_status": None,
        }

    async def fake_run_pages(urls_, on_result, *, stop_event=None):
        for i, u in enumerate(urls_):
            status = 403 if i == 3 else 200
            errors = ["HTTP 403"] if status == 403 else []
            await on_result(PageResult(url=u, requested_url=u, status_code=status, errors=errors))

    monkeypatch.setattr(jobs_module, "discover_audit_urls", fake_discover_audit_urls)
    monkeypatch.setattr(jobs_module, "run_pages", fake_run_pages)

    manager = JobManager()
    job = Job(job_id="single-403-job", requested_url="https://example.ru/")
    await manager._run(job)

    assert job.status == "completed"
    assert job.blocked_mid_audit is False
    assert job.checked_urls == 10
    assert job.errors_found == 1


async def test_entry_page_access_blocked_still_stops_before_any_page_checks(monkeypatch):
    """Test 13 from the plan: HTTP 403 on the entry URL itself must still
    stop discovery entirely and surface access_blocked_status, unchanged by
    today's work.
    """
    monkeypatch.setattr(jobs_module, "AUDIT_TIMEOUT", 30)

    async def fake_discover_audit_urls(url):
        return {
            "normalized_url": "https://example.ru/",
            "robots_url": None,
            "robots_found": None,
            "robots_sitemap_urls": [],
            "sitemap_urls": [],
            "sitemap_issues": [],
            "urls": [],
            "limited": False,
            "access_blocked_status": 403,
        }

    monkeypatch.setattr(jobs_module, "discover_audit_urls", fake_discover_audit_urls)

    manager = JobManager()
    job = Job(job_id="blocked-entry-job", requested_url="https://example.ru/")
    await manager._run(job)

    assert job.status == "completed"
    assert job.access_blocked_status == 403
    assert job.checked_urls == 0
    assert job.results == []


async def test_status_and_results_model_serialize_with_real_pydantic(monkeypatch):
    """Response models (AuditJobStatus/AuditResultsResponse) are only ever
    constructed inside JobManager.status_model/results_model, never
    directly in the other tests in this suite - so a type mismatch on any
    of today's *new* fields (sitemap_issues, blocked_mid_audit,
    mid_audit_block_status, partial_reason, the "completed_partial"
    status) would not be caught by those tests at all. Exercise both
    methods directly, with every new field populated, against the real
    Pydantic models to close that gap.
    """
    monkeypatch.setattr(jobs_module, "AUDIT_TIMEOUT", 0.05)

    async def fake_discover_audit_urls(url):
        return {
            "normalized_url": "https://example.ru/",
            "robots_url": "https://example.ru/robots.txt",
            "robots_found": True,
            "robots_sitemap_urls": ["https://example.ru/sitemap.xml"],
            "sitemap_urls": ["https://example.ru/sitemap.xml"],
            "sitemap_issues": ["https://example.ru/sitemap2.xml.gz: corrupted archive"],
            "urls": ["https://example.ru/a", "https://example.ru/b", "https://example.ru/c"],
            "limited": False,
            "access_blocked_status": None,
        }

    async def fake_run_pages(urls, on_result, *, stop_event=None):
        await on_result(PageResult(url=urls[0], requested_url=urls[0], status_code=200))
        await on_result(PageResult(url=urls[1], requested_url=urls[1], status_code=403, errors=["HTTP 403"]))
        await asyncio.sleep(10)  # force AUDIT_TIMEOUT -> completed_partial, with blocked_mid_audit still False here

    monkeypatch.setattr(jobs_module, "discover_audit_urls", fake_discover_audit_urls)
    monkeypatch.setattr(jobs_module, "run_pages", fake_run_pages)

    manager = JobManager()
    job = Job(job_id="model-serialization-job", requested_url="https://example.ru/")
    await manager._run(job)

    assert job.status == "completed_partial"

    # This is the exact call the real /api/audits/{id} endpoint makes -
    # must not raise a pydantic ValidationError with real Pydantic.
    status = manager.status_model(job)
    assert status.status == "completed_partial"
    assert status.sitemap_issues == ["https://example.ru/sitemap2.xml.gz: corrupted archive"]
    assert status.blocked_mid_audit is False
    assert status.mid_audit_block_status is None
    assert status.partial_reason is not None
    assert status.error is None

    # Likewise for /api/audits/{id}/results.
    results = manager.results_model(job)
    assert results.status == "completed_partial"
    assert len(results.results) == 2
    assert all(isinstance(r, PageResult) for r in results.results)


async def test_status_model_with_mid_audit_block_fields_populated():
    """Same coverage gap as above, but for the blocked_mid_audit /
    mid_audit_block_status fields specifically (item 9)."""
    manager = JobManager()
    job = Job(
        job_id="blocked-mid-audit-job",
        requested_url="https://example.ru/",
        status="completed_partial",
        discovered_urls=50,
        checked_urls=30,
        blocked_mid_audit=True,
        mid_audit_block_status=403,
        partial_reason="Сайт начал ограничивать автоматические запросы HeadInspect во время проверки.",
    )

    status = manager.status_model(job)
    assert status.blocked_mid_audit is True
    assert status.mid_audit_block_status == 403
    assert status.partial_reason.startswith("Сайт начал ограничивать")

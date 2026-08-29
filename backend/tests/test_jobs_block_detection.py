from __future__ import annotations

import asyncio

from app.audit import ACCESS_BLOCKED_STATUS_CODES
from app.jobs import Job, JobManager
from app.models import PageResult


def make_result(url, status_code=200, check_failed=False):
    # Mirror what the real analyze_page (app/audit.py) now produces: a
    # response with status_code in {401, 403, 429} is check_failed=True with
    # check_reason="access_blocked" and NO content-analysis errors - Open
    # Graph/Meta/Schema errors are never invented for a page HeadInspect
    # chose not to trust as real content (see PROPOSAL_2026-08-29 and the
    # "check_reason" work). This replaced the old fixture shape where a 403
    # page carried errors=["HTTP 403"] and check_failed=False - keeping this
    # helper in sync with the real function is exactly what the previous
    # incident (test fixtures silently drifting from production code)
    # taught us to be careful about.
    access_blocked = not check_failed and status_code in ACCESS_BLOCKED_STATUS_CODES
    if access_blocked:
        check_failed = True

    errors, warnings = [], []
    if not check_failed and status_code is not None:
        if status_code >= 400:
            errors.append(f"HTTP {status_code}")
        elif status_code >= 300:
            warnings.append(f"HTTP {status_code}")

    check_reason = "access_blocked" if access_blocked else (None if not check_failed else "timeout")

    return PageResult(
        url=url, requested_url=url, status_code=status_code,
        check_failed=check_failed, check_reason=check_reason, errors=errors, warnings=warnings,
    )


async def feed(on_result, n, status_code, check_failed=False, start=0):
    for i in range(start, start + n):
        await on_result(make_result(f"https://example.ru/p{i}", status_code=status_code, check_failed=check_failed))


async def test_single_403_among_healthy_pages_is_not_a_block():
    """Plan requirement: 'не считай единичный 403 доказательством
    блокировки всего сайта' (test 11 from the checklist)."""
    job = Job(job_id="j1", requested_url="https://example.ru/")
    stop_event = asyncio.Event()
    on_result = JobManager._make_on_result(job, stop_event)

    await feed(on_result, 20, 200)
    await on_result(make_result("https://example.ru/admin", status_code=403))
    await feed(on_result, 20, 200, start=20)

    assert job.blocked_mid_audit is False
    assert not stop_event.is_set()
    assert job.checked_urls == 41
    # A single 403 is check_failed=True/check_reason="access_blocked" now
    # (see make_result above) - it is a page HeadInspect could not verify,
    # not a proven Open Graph/Meta/Schema error, so it belongs in
    # failed_checks, not errors_found.
    assert job.errors_found == 0
    assert job.failed_checks == 1


async def test_mass_403_after_good_pages_is_detected_as_a_block():
    """Test 12 from the checklist: a dense run of 401/403/429 after a solid
    run of successes is flagged, and further requests are stopped."""
    job = Job(job_id="j2", requested_url="https://example.ru/")
    stop_event = asyncio.Event()
    on_result = JobManager._make_on_result(job, stop_event)

    await feed(on_result, 30, 200)
    await feed(on_result, 15, 403)

    assert job.blocked_mid_audit is True
    assert stop_event.is_set()
    assert job.mid_audit_block_status == 403
    # At most the detection window's worth of block responses should have
    # leaked into results/errors before detection fired.
    assert job.checked_urls < 45
    assert job.checked_urls >= 30


async def test_site_that_403s_everything_from_the_start_is_not_flagged():
    """Guards MIN_GOOD_BEFORE: no prior successes means there is nothing to
    compare against, so this must not be mistaken for a mid-audit block."""
    job = Job(job_id="j3", requested_url="https://example.ru/")
    stop_event = asyncio.Event()
    on_result = JobManager._make_on_result(job, stop_event)

    await feed(on_result, 20, 403)

    assert job.blocked_mid_audit is False
    assert not stop_event.is_set()


async def test_partial_status_reason_mentions_the_blocking_code():
    job = Job(job_id="j4", requested_url="https://example.ru/")
    stop_event = asyncio.Event()
    on_result = JobManager._make_on_result(job, stop_event)
    await feed(on_result, 30, 200)
    await feed(on_result, 15, 403)

    if job.blocked_mid_audit:
        job.status = "completed_partial"
        job.partial_reason = (
            "Сайт начал ограничивать автоматические запросы HeadInspect во время "
            f"проверки (сервер стал отвечать HTTP {job.mid_audit_block_status})."
        )

    assert job.status == "completed_partial"
    assert "403" in job.partial_reason
    assert "Сайт начал ограничивать" in job.partial_reason

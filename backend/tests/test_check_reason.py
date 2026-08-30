"""Tests for `check_reason` (items 2, 3, 5 of the updated task): a
check_failed page now carries a structured reason instead of forcing every
consumer to pattern-match `check_error` text, and a page that HeadInspect
chose not to trust (401/403/429) must never look like a normally-analyzed
page with content errors inside it.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app import audit as audit_module
from app.jobs import Job, JobManager
from app.models import PageResult


class FakeFetchResult:
    def __init__(self, url, status_code=200, headers=None, content=b""):
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content


@pytest.mark.parametrize("status_code", [401, 403, 429])
async def test_access_blocked_response_skips_content_analysis(monkeypatch, status_code):
    calls = {"og": 0, "meta": 0, "schema": 0}

    async def fake_safe_fetch(url, **kwargs):
        return FakeFetchResult(
            url=url, status_code=status_code,
            headers={"content-type": "text/html"},
            content=b"<html><head><title>Verification required</title></head><body></body></html>",
        )

    async def fake_analyze_open_graph(*a, **kw):
        calls["og"] += 1
        raise AssertionError("must not be called for an access_blocked page")

    def fake_analyze_meta(*a, **kw):
        calls["meta"] += 1
        raise AssertionError("must not be called for an access_blocked page")

    def fake_analyze_schema(*a, **kw):
        calls["schema"] += 1
        raise AssertionError("must not be called for an access_blocked page")

    monkeypatch.setattr(audit_module, "safe_fetch", fake_safe_fetch)
    monkeypatch.setattr(audit_module, "analyze_open_graph", fake_analyze_open_graph)
    monkeypatch.setattr(audit_module, "analyze_meta", fake_analyze_meta)
    monkeypatch.setattr(audit_module, "analyze_schema", fake_analyze_schema)

    result = await audit_module.analyze_page("https://example.ru/page", asyncio.Semaphore(1))

    assert calls == {"og": 0, "meta": 0, "schema": 0}
    assert result.check_failed is True
    assert result.check_reason == "access_blocked"
    # status_code is preserved (unlike genuine network/timeout failures) -
    # Sitemap needs this to keep showing "URL недоступен: HTTP 403".
    assert result.status_code == status_code
    assert result.errors == []
    assert result.warnings == []
    # The page's own title is kept as a diagnostic detail, never used to
    # classify the page (no content-sniffing of specific WAF vendors).
    assert result.title == "Verification required"
    assert "Verification required" in result.check_error


async def test_access_blocked_without_a_title_still_gets_a_clear_reason(monkeypatch):
    async def fake_safe_fetch(url, **kwargs):
        return FakeFetchResult(url=url, status_code=403, headers={"content-type": "text/html"}, content=b"<html></html>")

    monkeypatch.setattr(audit_module, "safe_fetch", fake_safe_fetch)

    result = await audit_module.analyze_page("https://example.ru/page", asyncio.Semaphore(1))

    assert result.check_reason == "access_blocked"
    assert result.title is None
    assert "403" in result.check_error


async def test_ordinary_200_response_is_unaffected(monkeypatch):
    async def fake_safe_fetch(url, **kwargs):
        return FakeFetchResult(
            url=url, status_code=200, headers={"content-type": "text/html"},
            content=b"<html><head><title>Real page</title></head></html>",
        )

    monkeypatch.setattr(audit_module, "safe_fetch", fake_safe_fetch)

    result = await audit_module.analyze_page("https://example.ru/page", asyncio.Semaphore(1))

    assert result.check_failed is False
    assert result.check_reason is None
    assert result.title == "Real page"


@pytest.mark.parametrize(
    "exc,expected_reason",
    [
        (HTTPException(status_code=504, detail="Timeout while fetching https://example.ru/"), "timeout"),
        (HTTPException(status_code=502, detail="Unexpected content type: image/jpeg"), "content_type"),
        (HTTPException(status_code=502, detail="Remote response is too large"), "content_type"),
        (HTTPException(status_code=502, detail="Cannot fetch https://example.ru/"), "network"),
        (HTTPException(status_code=400, detail="Missing hostname"), "network"),
        (HTTPException(status_code=502, detail="Too many redirects"), "network"),
    ],
)
async def test_fetch_failure_reason_classification(monkeypatch, exc, expected_reason):
    async def fake_safe_fetch(url, **kwargs):
        raise exc

    monkeypatch.setattr(audit_module, "safe_fetch", fake_safe_fetch)

    result = await audit_module.analyze_page("https://example.ru/page", asyncio.Semaphore(1))

    assert result.check_failed is True
    assert result.check_reason == expected_reason
    assert result.status_code is None  # genuine transport failures never have a status_code


async def test_page_timeout_wrapper_sets_timeout_reason(monkeypatch):
    """Item 7.2: a URL that DID acquire its semaphore slot (real processing
    started) but whose fetch-and-analyze work doesn't finish within
    PAGE_TIMEOUT must be reported as check_reason='timeout'. Mocks
    _fetch_and_analyze (the inner work function PAGE_TIMEOUT now wraps),
    not analyze_page itself - analyze_page is real here, exercising its own
    wait_for/semaphore logic end to end."""
    async def hang_forever(url):
        await asyncio.sleep(10)

    monkeypatch.setattr(audit_module, "_fetch_and_analyze", hang_forever)
    monkeypatch.setattr(audit_module, "PAGE_TIMEOUT", 0.05)

    seen = []

    async def on_result(result):
        seen.append(result)

    await audit_module.run_pages(["https://example.ru/slow"], on_result)

    assert len(seen) == 1
    assert seen[0].check_failed is True
    assert seen[0].check_reason == "timeout"
    assert seen[0].status_code is None


async def test_mass_access_blocked_still_triggers_block_detection(monkeypatch):
    """Critical regression guard (item 6 / section 2.4 of the proposal):
    access_blocked pages are now check_failed=True, which would silently
    disable block detection if jobs.py's is_block_response still excluded
    check_failed results. Exercises the real analyze_page -> on_result path
    together, not just one side in isolation.
    """
    call_count = {"n": 0}

    async def fake_safe_fetch(url, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 30:
            return FakeFetchResult(url=url, status_code=200, headers={"content-type": "text/html"}, content=b"<html></html>")
        return FakeFetchResult(url=url, status_code=403, headers={"content-type": "text/html"}, content=b"<html></html>")

    monkeypatch.setattr(audit_module, "safe_fetch", fake_safe_fetch)

    job = Job(job_id="mass-block-real-analyze-page", requested_url="https://example.ru/")
    stop_event = asyncio.Event()
    on_result = JobManager._make_on_result(job, stop_event)

    urls = [f"https://example.ru/p{i}" for i in range(60)]
    for url in urls:
        result = await audit_module.analyze_page(url, asyncio.Semaphore(4), stop_event=stop_event)
        if result is not None:
            await on_result(result)
        if stop_event.is_set():
            break

    assert job.blocked_mid_audit is True
    assert job.mid_audit_block_status == 403
    assert stop_event.is_set()


async def test_mass_timeouts_never_trigger_block_detection(monkeypatch):
    """radov39.ru regression guard: a large run of consecutive timeouts
    (check_reason='timeout', status_code=None) must never be mistaken for
    a mass-block (item 4 of the updated task). is_block_response only fires
    for status_code in BLOCK_DETECT_STATUS_CODES, and a timeout's
    status_code is always None - this proves that holds with the real
    analyze_page + on_result pipeline, not just in isolation.
    """
    call_count = {"n": 0}

    async def fake_safe_fetch(url, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 20:
            return FakeFetchResult(url=url, status_code=200, headers={"content-type": "text/html"}, content=b"<html></html>")
        raise HTTPException(status_code=504, detail=f"Timeout while fetching {url}")

    monkeypatch.setattr(audit_module, "safe_fetch", fake_safe_fetch)

    job = Job(job_id="mass-timeout-radov39-shaped", requested_url="https://radov39.ru/")
    stop_event = asyncio.Event()
    on_result = JobManager._make_on_result(job, stop_event)

    urls = [f"https://radov39.ru/p{i}" for i in range(50)]
    for url in urls:
        result = await audit_module.analyze_page(url, asyncio.Semaphore(4), stop_event=stop_event)
        if result is not None:
            await on_result(result)

    assert job.blocked_mid_audit is False
    assert not stop_event.is_set()
    # checked_urls counts every page that was actually attempted (both
    # successes and check_failed ones); failed_checks is the subset that
    # couldn't be verified.
    assert job.checked_urls == 50
    assert job.failed_checks == 30  # the 30 timeouts
    assert len(job.results) == 50
    timeout_results = [r for r in job.results if r.check_reason == "timeout"]
    assert len(timeout_results) == 30
    success_results = [r for r in job.results if r.check_reason is None]
    assert len(success_results) == 20

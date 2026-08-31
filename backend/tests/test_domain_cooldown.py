"""P0: domain cooldown for repeated audits of the same site.

See the module-level comment above BLOCK_DETECT_STATUS_CODES in
backend/app/jobs.py for the full semantics this implements and why:
site key = normalize_public_url()'s hostname only (scheme/port/path/
query/fragment ignored, www/non-www kept separate); cooldown starts at
job *creation*; it is not lifted for failed/completed_partial jobs.

Every test here patches JobManager._run to a no-op so create() is
exercised without any real network/background work - only the
queue/rate-limit/cooldown gating logic in create() itself is under test.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.jobs import JobManager


async def _noop_run(self, job) -> None:  # pragma: no cover - trivial
    return None


def make_manager(monkeypatch) -> JobManager:
    manager = JobManager()
    monkeypatch.setattr(JobManager, "_run", _noop_run)
    return manager


async def test_first_audit_of_a_domain_is_allowed(monkeypatch):
    manager = make_manager(monkeypatch)
    job = await manager.create("https://example.ru/", client_ip="1.1.1.1")
    assert job.requested_url == "https://example.ru/"


async def test_repeat_audit_within_cooldown_is_blocked(monkeypatch):
    manager = make_manager(monkeypatch)
    await manager.create("https://example.ru/", client_ip="1.1.1.1")

    with pytest.raises(HTTPException) as exc_info:
        await manager.create("https://example.ru/", client_ip="2.2.2.2")

    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers
    assert "сайт" in exc_info.value.detail.lower()


async def test_repeat_audit_after_cooldown_expires_is_allowed(monkeypatch):
    import app.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "DOMAIN_COOLDOWN_SECONDS", 0.05)
    manager = make_manager(monkeypatch)

    await manager.create("https://example.ru/", client_ip="1.1.1.1")

    import asyncio

    await asyncio.sleep(0.1)

    job = await manager.create("https://example.ru/", client_ip="1.1.1.1")
    assert job.requested_url == "https://example.ru/"


async def test_independent_domains_do_not_block_each_other(monkeypatch):
    manager = make_manager(monkeypatch)
    await manager.create("https://example.ru/", client_ip="1.1.1.1")

    job = await manager.create("https://another-site.ru/", client_ip="1.1.1.1")
    assert job.requested_url == "https://another-site.ru/"


async def test_hostname_case_does_not_bypass_cooldown(monkeypatch):
    manager = make_manager(monkeypatch)
    await manager.create("https://EXAMPLE.ru/", client_ip="1.1.1.1")

    with pytest.raises(HTTPException) as exc_info:
        await manager.create("https://example.RU/", client_ip="1.1.1.1")
    assert exc_info.value.status_code == 429


async def test_different_path_does_not_bypass_cooldown(monkeypatch):
    manager = make_manager(monkeypatch)
    await manager.create("https://example.ru/", client_ip="1.1.1.1")

    with pytest.raises(HTTPException):
        await manager.create("https://example.ru/some/other/page", client_ip="1.1.1.1")


async def test_query_string_does_not_bypass_cooldown(monkeypatch):
    manager = make_manager(monkeypatch)
    await manager.create("https://example.ru/", client_ip="1.1.1.1")

    with pytest.raises(HTTPException):
        await manager.create("https://example.ru/?utm_source=x", client_ip="1.1.1.1")


async def test_fragment_does_not_bypass_cooldown(monkeypatch):
    manager = make_manager(monkeypatch)
    await manager.create("https://example.ru/", client_ip="1.1.1.1")

    with pytest.raises(HTTPException):
        await manager.create("https://example.ru/#section", client_ip="1.1.1.1")


async def test_http_and_https_share_the_same_cooldown(monkeypatch):
    """Chosen semantics (see module comment in jobs.py): scheme alone does
    not identify a different site, so http/https of the same hostname
    share one cooldown - otherwise the cooldown would be trivially
    bypassed by just editing the URL's scheme."""
    manager = make_manager(monkeypatch)
    await manager.create("http://example.ru/", client_ip="1.1.1.1")

    with pytest.raises(HTTPException):
        await manager.create("https://example.ru/", client_ip="1.1.1.1")


async def test_www_and_non_www_are_independent_cooldowns(monkeypatch):
    """Chosen semantics (see module comment in jobs.py): HeadInspect never
    aliases www/non-www from the URL string alone anywhere else in the
    codebase (see tests/test_www_nonwww.py) - that equivalence is only
    ever established by actually following a same-site redirect during a
    real audit run, which the cooldown gate (a pre-flight, no-network
    check) cannot do. So www.example.ru and example.ru get independent
    cooldowns here, matching the existing behavior rather than inventing
    a new rule."""
    manager = make_manager(monkeypatch)
    await manager.create("https://example.ru/", client_ip="1.1.1.1")

    job = await manager.create("https://www.example.ru/", client_ip="1.1.1.1")
    assert job.requested_url == "https://www.example.ru/"


async def test_cooldown_applies_regardless_of_client_ip(monkeypatch):
    """The cooldown protects the *audited site*, not the caller - it must
    not be bypassable by simply using a different IP."""
    manager = make_manager(monkeypatch)
    await manager.create("https://example.ru/", client_ip="1.1.1.1")

    with pytest.raises(HTTPException):
        await manager.create("https://example.ru/", client_ip="9.9.9.9")


async def test_failed_job_still_enforces_cooldown(monkeypatch):
    """A job that ends up `failed` already made real requests against the
    site before failing - the cooldown recorded at creation time is not
    lifted just because the job later failed."""
    import asyncio

    manager = JobManager()

    async def fail_run(self, job) -> None:
        job.status = "failed"
        job.error = "boom"

    monkeypatch.setattr(JobManager, "_run", fail_run)

    job = await manager.create("https://example.ru/", client_ip="1.1.1.1")
    await asyncio.sleep(0)  # let the asyncio.create_task'd _run actually execute
    assert job.status == "failed"

    with pytest.raises(HTTPException) as exc_info:
        await manager.create("https://example.ru/", client_ip="1.1.1.1")
    assert exc_info.value.status_code == 429


async def test_completed_partial_job_still_enforces_cooldown(monkeypatch):
    import asyncio

    manager = JobManager()

    async def partial_run(self, job) -> None:
        job.status = "completed_partial"
        job.partial_reason = "just because"

    monkeypatch.setattr(JobManager, "_run", partial_run)

    job = await manager.create("https://example.ru/", client_ip="1.1.1.1")
    await asyncio.sleep(0)
    assert job.status == "completed_partial"

    with pytest.raises(HTTPException) as exc_info:
        await manager.create("https://example.ru/", client_ip="1.1.1.1")
    assert exc_info.value.status_code == 429


async def test_invalid_url_is_not_blocked_by_cooldown_machinery(monkeypatch):
    """A URL that fails even normalize_public_url() (here: a disallowed
    scheme) never reaches the site at all, so it must not be treated as
    consuming (or being blocked by) a domain cooldown slot - it fails the
    same way it already did before this feature, later, once the job
    actually runs and validate_public_url is called."""
    manager = make_manager(monkeypatch)

    job1 = await manager.create("ftp://example.ru/", client_ip="1.1.1.1")
    job2 = await manager.create("ftp://example.ru/", client_ip="1.1.1.1")
    assert job1.job_id != job2.job_id

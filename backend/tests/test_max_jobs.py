"""P0: safe upper bound on the number of Job objects kept in memory.

See config.MAX_JOBS and JobManager.cleanup's MAX_JOBS handling in
backend/app/jobs.py. Eviction criterion is deliberately identical to the
existing TTL criterion (job.completed_at is not None) so queued/running/
discovering jobs are never candidates, oldest-finished-first, and it runs
inside the same cleanup() pass (and the same _cleanup_lock) as TTL rather
than as a second, independently-scheduled mechanism.

Most tests here populate `manager.jobs` directly (bypassing create()/_run)
so job age/status can be controlled precisely and cheaply.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import app.jobs as jobs_module
from app.jobs import Job, JobManager


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_finished_job(job_id: str, *, age_seconds: float, status: str = "completed") -> Job:
    completed = utcnow() - timedelta(seconds=age_seconds)
    return Job(
        job_id=job_id,
        requested_url=f"https://example.ru/{job_id}",
        status=status,  # type: ignore[arg-type]
        completed_at=completed,
        started_at=completed - timedelta(seconds=5),
    )


def make_active_job(job_id: str, *, status: str) -> Job:
    return Job(
        job_id=job_id,
        requested_url=f"https://example.ru/{job_id}",
        status=status,  # type: ignore[arg-type]
    )


async def test_jobs_under_the_limit_are_left_alone(monkeypatch):
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 10)
    manager = JobManager()
    for i in range(5):
        job = make_finished_job(f"job-{i}", age_seconds=i)
        manager.jobs[job.job_id] = job

    await manager.cleanup()

    assert len(manager.jobs) == 5


async def test_oldest_finished_jobs_are_evicted_first_at_the_limit(monkeypatch):
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 5)
    manager = JobManager()
    # 8 finished jobs, ages 0..7 (job-7 is the oldest - largest age_seconds).
    for i in range(8):
        job = make_finished_job(f"job-{i}", age_seconds=i)
        manager.jobs[job.job_id] = job

    await manager.cleanup()

    assert len(manager.jobs) == 5
    # The 3 oldest (job-5, job-6, job-7) must be the ones gone.
    assert "job-7" not in manager.jobs
    assert "job-6" not in manager.jobs
    assert "job-5" not in manager.jobs
    # The 5 newest must remain.
    for i in range(5):
        assert f"job-{i}" in manager.jobs


async def test_queued_job_is_never_evicted_by_max_jobs(monkeypatch):
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 3)
    manager = JobManager()
    queued = make_active_job("the-queued-one", status="queued")
    manager.jobs[queued.job_id] = queued
    for i in range(5):
        job = make_finished_job(f"finished-{i}", age_seconds=i)
        manager.jobs[job.job_id] = job

    await manager.cleanup()

    assert "the-queued-one" in manager.jobs
    assert len(manager.jobs) == 3  # queued + 2 newest finished


async def test_running_job_is_never_evicted_by_max_jobs(monkeypatch):
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 3)
    manager = JobManager()
    running = make_active_job("the-running-one", status="running")
    manager.jobs[running.job_id] = running
    for i in range(5):
        job = make_finished_job(f"finished-{i}", age_seconds=i)
        manager.jobs[job.job_id] = job

    await manager.cleanup()

    assert "the-running-one" in manager.jobs
    assert len(manager.jobs) == 3


async def test_discovering_job_is_never_evicted_by_max_jobs(monkeypatch):
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 3)
    manager = JobManager()
    discovering = make_active_job("the-discovering-one", status="discovering")
    manager.jobs[discovering.job_id] = discovering
    for i in range(5):
        job = make_finished_job(f"finished-{i}", age_seconds=i)
        manager.jobs[job.job_id] = job

    await manager.cleanup()

    assert "the-discovering-one" in manager.jobs


async def test_ttl_still_works_alongside_max_jobs(monkeypatch):
    """Item 5/6: TTL and MAX_JOBS are not two competing mechanisms - a job
    past its TTL is removed by the existing TTL pass first, so it's simply
    already gone by the time the MAX_JOBS pass runs; nothing double-counts
    or conflicts."""
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 100)
    monkeypatch.setattr(jobs_module, "JOB_TTL_SECONDS", 60)
    manager = JobManager()

    stale = make_finished_job("stale-job", age_seconds=120)  # past TTL
    fresh = make_finished_job("fresh-job", age_seconds=5)
    manager.jobs[stale.job_id] = stale
    manager.jobs[fresh.job_id] = fresh

    await manager.cleanup()

    assert "stale-job" not in manager.jobs
    assert "fresh-job" in manager.jobs


async def test_max_jobs_eviction_does_not_remove_jobs_still_within_ttl(monkeypatch):
    """MAX_JOBS eviction can remove a finished job *before* its TTL expires
    - that's the whole point of the extra cap - but must never touch an
    active job just to make room."""
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 2)
    monkeypatch.setattr(jobs_module, "JOB_TTL_SECONDS", 3600)
    manager = JobManager()
    for i in range(4):
        job = make_finished_job(f"job-{i}", age_seconds=i)  # all well within TTL
        manager.jobs[job.job_id] = job

    await manager.cleanup()

    # None of these are TTL-expired, but MAX_JOBS still trims down to 2.
    assert len(manager.jobs) == 2
    assert "job-0" in manager.jobs
    assert "job-1" in manager.jobs


async def test_at_the_limit_with_nothing_evictable_cleanup_does_not_crash(monkeypatch):
    """Item 7: if every job is active (queued/running), cleanup() must not
    raise and must not touch any of them - it simply leaves the count over
    the cap rather than destroying an active audit. (Not reachable in
    practice while MAX_QUEUED_AUDITS + MAX_CONCURRENT_AUDITS stays far
    below MAX_JOBS, but must still be safe.)"""
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 3)
    manager = JobManager()
    for i in range(5):
        job = make_active_job(f"active-{i}", status="running")
        manager.jobs[job.job_id] = job

    await manager.cleanup()  # must not raise

    assert len(manager.jobs) == 5
    assert all(f"active-{i}" in manager.jobs for i in range(5))


async def test_create_rejects_new_jobs_once_truly_full_of_active_jobs(monkeypatch):
    """The create()-time backstop: if cleanup() could not free any room
    (everything genuinely active), a brand new job must be refused with a
    clean, structured error rather than pushing the dict past MAX_JOBS or
    raising an unhandled exception."""
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 3)
    manager = JobManager()
    for i in range(3):
        job = make_active_job(f"active-{i}", status="running")
        manager.jobs[job.job_id] = job

    async def _noop_run(self, job) -> None:  # pragma: no cover - unreachable here
        return None

    monkeypatch.setattr(JobManager, "_run", _noop_run)

    with pytest.raises(HTTPException) as exc_info:
        await manager.create("https://another-example.ru/", client_ip="1.1.1.1")

    assert exc_info.value.status_code == 503
    assert len(manager.jobs) == 3  # unchanged - no partial/broken job left behind


async def test_job_count_never_exceeds_max_jobs_after_repeated_cleanup(monkeypatch):
    """Item 8: however many finished jobs accumulate, repeated cleanup()
    calls keep the dict at or under MAX_JOBS - it can't creep upward."""
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 10)
    manager = JobManager()

    for batch in range(3):
        for i in range(15):
            job = make_finished_job(f"batch{batch}-job{i}", age_seconds=i)
            manager.jobs[job.job_id] = job
        await manager.cleanup()
        assert len(manager.jobs) <= 10


# --- Regression tests: boundary case at exactly MAX_JOBS ------------------
#
# cleanup()'s bulk MAX_JOBS eviction only fires while strictly *over* the
# cap (`len(self.jobs) > MAX_JOBS`) - by design, cleanup() on its own has
# no reason to assume a new job is about to be added, so sitting exactly
# *at* the cap is left alone there. The bug: create()'s backstop used to
# treat "at cap after cleanup()" as "nothing evictable, reject with 503",
# even when a perfectly evictable finished job was sitting right there -
# e.g. 199 completed + 1 running = 200 (MAX_JOBS) should free one slot by
# evicting the oldest completed job, not refuse the new audit outright.
# create() now makes room for exactly the one job it's about to create by
# evicting the single oldest safely-finished job itself, falling back to
# the 503 only when every job at the cap is genuinely active.


async def test_create_frees_one_slot_at_exactly_max_jobs_when_one_is_evictable(monkeypatch):
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 200)
    manager = JobManager()
    for i in range(199):
        job = make_finished_job(f"completed-{i}", age_seconds=i)
        manager.jobs[job.job_id] = job
    running = make_active_job("the-running-one", status="running")
    manager.jobs[running.job_id] = running
    assert len(manager.jobs) == 200

    async def _noop_run(self, job) -> None:  # pragma: no cover - trivial
        return None

    monkeypatch.setattr(JobManager, "_run", _noop_run)

    job = await manager.create("https://another-example.ru/", client_ip="1.1.1.1")

    assert job.job_id in manager.jobs
    assert "the-running-one" in manager.jobs  # active job untouched
    assert len(manager.jobs) == 200  # one evicted, one created - net unchanged


async def test_create_evicts_the_oldest_completed_job_specifically(monkeypatch):
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 3)
    manager = JobManager()
    # age_seconds=i means job-2 (age 2) is the oldest, job-0 the newest.
    for i in range(3):
        job = make_finished_job(f"completed-{i}", age_seconds=i)
        manager.jobs[job.job_id] = job

    async def _noop_run(self, job) -> None:  # pragma: no cover - trivial
        return None

    monkeypatch.setattr(JobManager, "_run", _noop_run)

    await manager.create("https://another-example.ru/", client_ip="1.1.1.1")

    assert "completed-2" not in manager.jobs  # the oldest - gone
    assert "completed-1" in manager.jobs
    assert "completed-0" in manager.jobs
    assert len(manager.jobs) == 3


async def test_create_still_returns_503_when_every_job_at_the_cap_is_active(monkeypatch):
    """Unchanged behavior: exactly MAX_JOBS, but none of them are safely
    finished - no slot can be freed without touching an active audit, so
    the request is refused, not silently pushed over the cap."""
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 3)
    manager = JobManager()
    statuses = ["queued", "running", "discovering"]
    for status in statuses:
        job = make_active_job(f"active-{status}", status=status)
        manager.jobs[job.job_id] = job
    assert len(manager.jobs) == 3

    async def _noop_run(self, job) -> None:  # pragma: no cover - unreachable here
        return None

    monkeypatch.setattr(JobManager, "_run", _noop_run)

    with pytest.raises(HTTPException) as exc_info:
        await manager.create("https://another-example.ru/", client_ip="1.1.1.1")

    assert exc_info.value.status_code == 503
    assert len(manager.jobs) == 3
    for status in statuses:
        assert f"active-{status}" in manager.jobs


async def test_job_count_never_exceeds_max_jobs_after_create_at_the_boundary(monkeypatch):
    """Item 4: after a successful create() that had to free a slot at
    exactly MAX_JOBS, the dict must not end up over the cap."""
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 5)
    manager = JobManager()
    for i in range(5):
        job = make_finished_job(f"completed-{i}", age_seconds=i)
        manager.jobs[job.job_id] = job

    async def _noop_run(self, job) -> None:  # pragma: no cover - trivial
        return None

    monkeypatch.setattr(JobManager, "_run", _noop_run)

    await manager.create("https://another-example.ru/", client_ip="1.1.1.1")

    assert len(manager.jobs) <= 5


async def test_active_jobs_survive_slot_freeing_at_the_boundary(monkeypatch):
    """Item 5: when create() frees a slot at exactly MAX_JOBS, any
    queued/running/discovering jobs mixed in with the finished ones must
    all survive - only the oldest *finished* job is ever evicted."""
    monkeypatch.setattr(jobs_module, "MAX_JOBS", 6)
    manager = JobManager()
    for i in range(3):
        job = make_finished_job(f"completed-{i}", age_seconds=i)
        manager.jobs[job.job_id] = job
    for status in ("queued", "running", "discovering"):
        job = make_active_job(f"active-{status}", status=status)
        manager.jobs[job.job_id] = job
    assert len(manager.jobs) == 6

    async def _noop_run(self, job) -> None:  # pragma: no cover - trivial
        return None

    monkeypatch.setattr(JobManager, "_run", _noop_run)

    await manager.create("https://another-example.ru/", client_ip="1.1.1.1")

    for status in ("queued", "running", "discovering"):
        assert f"active-{status}" in manager.jobs
    # The oldest completed job (age 2) is the one that made room.
    assert "completed-2" not in manager.jobs
    assert "completed-1" in manager.jobs
    assert "completed-0" in manager.jobs
    assert len(manager.jobs) == 6

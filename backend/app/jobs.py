from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import uuid

from fastapi import HTTPException

from .audit import discover_audit_urls, run_pages
from .config import JOB_TTL_SECONDS, MAX_AUDIT_URLS, MAX_CONCURRENT_AUDITS
from .models import AuditJobStatus, AuditResultsResponse, JobStatus, PageResult


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Job:
    job_id: str
    requested_url: str
    status: JobStatus = "queued"
    normalized_url: str | None = None
    robots_url: str | None = None
    robots_found: bool | None = None
    sitemap_urls: list[str] = field(default_factory=list)
    discovered_urls: int = 0
    checked_urls: int = 0
    limited: bool = False
    errors_found: int = 0
    warnings_found: int = 0
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    results: list[PageResult] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.audit_slots = asyncio.Semaphore(MAX_CONCURRENT_AUDITS)
        self._cleanup_lock = asyncio.Lock()

    async def create(self, requested_url: str) -> Job:
        await self.cleanup()
        job = Job(job_id=uuid.uuid4().hex, requested_url=requested_url)
        self.jobs[job.job_id] = job
        asyncio.create_task(self._run(job))
        return job

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Audit job not found")
        return job

    async def _run(self, job: Job) -> None:
        async with self.audit_slots:
            try:
                async with job.lock:
                    job.status = "discovering"
                    job.started_at = utcnow()

                discovered = await discover_audit_urls(job.requested_url)
                urls: list[str] = discovered["urls"]

                async with job.lock:
                    job.normalized_url = discovered["normalized_url"]
                    job.robots_url = discovered["robots_url"]
                    job.robots_found = discovered["robots_found"]
                    job.sitemap_urls = discovered["sitemap_urls"]
                    job.discovered_urls = len(urls)
                    job.limited = discovered["limited"]
                    job.status = "running"

                async def on_result(result: PageResult) -> None:
                    async with job.lock:
                        job.results.append(result)
                        job.checked_urls += 1
                        if result.errors:
                            job.errors_found += 1
                        elif result.warnings:
                            job.warnings_found += 1

                await run_pages(urls, on_result)

                async with job.lock:
                    job.status = "completed"
                    job.completed_at = utcnow()

            except HTTPException as exc:
                async with job.lock:
                    job.status = "failed"
                    job.error = str(exc.detail)
                    job.completed_at = utcnow()
            except Exception:
                async with job.lock:
                    job.status = "failed"
                    job.error = "Audit failed"
                    job.completed_at = utcnow()

    def status_model(self, job: Job) -> AuditJobStatus:
        total = job.discovered_urls
        progress = 0
        if job.status == "completed":
            progress = 100
        elif total > 0:
            progress = min(99, round(job.checked_urls / total * 100))

        return AuditJobStatus(
            job_id=job.job_id,
            status=job.status,
            requested_url=job.requested_url,
            normalized_url=job.normalized_url,
            robots_url=job.robots_url,
            robots_found=job.robots_found,
            sitemap_urls=job.sitemap_urls,
            discovered_urls=job.discovered_urls,
            checked_urls=job.checked_urls,
            max_urls=MAX_AUDIT_URLS,
            limited=job.limited,
            progress_percent=progress,
            errors_found=job.errors_found,
            warnings_found=job.warnings_found,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            error=job.error,
        )

    def results_model(self, job: Job) -> AuditResultsResponse:
        return AuditResultsResponse(
            job_id=job.job_id,
            status=job.status,
            checked_urls=job.checked_urls,
            discovered_urls=job.discovered_urls,
            results=list(job.results),
        )

    async def cleanup(self) -> None:
        async with self._cleanup_lock:
            cutoff = utcnow() - timedelta(seconds=JOB_TTL_SECONDS)
            stale = [
                job_id
                for job_id, job in self.jobs.items()
                if job.completed_at is not None and job.completed_at < cutoff
            ]
            for job_id in stale:
                self.jobs.pop(job_id, None)


job_manager = JobManager()

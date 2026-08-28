from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import monotonic
import uuid

from fastapi import HTTPException

from .audit import discover_audit_urls, run_pages
from .config import (
    AUDIT_TIMEOUT,
    JOB_TTL_SECONDS,
    MAX_AUDIT_URLS,
    MAX_CONCURRENT_AUDITS,
    MAX_QUEUED_AUDITS,
    RATE_LIMIT_AUDITS,
    RATE_LIMIT_WINDOW_SECONDS,
)
from .models import AuditJobStatus, AuditResultsResponse, JobStatus, PageResult


logger = logging.getLogger("uvicorn.error")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _format_wait(seconds: int) -> str:
    seconds = max(1, seconds)
    minutes, seconds = divmod(seconds, 60)
    if minutes and seconds:
        return f"{minutes} мин {seconds} сек"
    if minutes:
        return f"{minutes} мин"
    return f"{seconds} сек"


@dataclass
class Job:
    job_id: str
    requested_url: str
    client_ip: str = "unknown"
    status: JobStatus = "queued"
    normalized_url: str | None = None
    robots_url: str | None = None
    robots_found: bool | None = None
    robots_sitemap_urls: list[str] = field(default_factory=list)
    sitemap_urls: list[str] = field(default_factory=list)
    discovered_urls: int = 0
    checked_urls: int = 0
    limited: bool = False
    errors_found: int = 0
    warnings_found: int = 0
    failed_checks: int = 0
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
        self._create_lock = asyncio.Lock()
        self._rate_attempts: dict[str, deque[float]] = defaultdict(deque)

    async def create(self, requested_url: str, *, client_ip: str) -> Job:
        await self.cleanup()

        async with self._create_lock:
            queued_count = sum(1 for job in self.jobs.values() if job.status == "queued")
            if queued_count >= MAX_QUEUED_AUDITS:
                logger.warning(
                    "Audit rejected: queue full (%s/%s), ip=%s, url=%s",
                    queued_count,
                    MAX_QUEUED_AUDITS,
                    client_ip,
                    requested_url,
                )
                raise HTTPException(
                    status_code=503,
                    detail="Сервис сейчас занят: очередь проверок заполнена. Попробуйте через несколько минут.",
                    headers={"Retry-After": "60"},
                )

            now = monotonic()
            attempts = self._rate_attempts[client_ip]
            cutoff = now - RATE_LIMIT_WINDOW_SECONDS
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()

            if len(attempts) >= RATE_LIMIT_AUDITS:
                retry_after = max(1, int(attempts[0] + RATE_LIMIT_WINDOW_SECONDS - now + 0.999))
                logger.warning(
                    "Audit rate limited: ip=%s, retry_after=%ss, url=%s",
                    client_ip,
                    retry_after,
                    requested_url,
                )
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Вы уже запустили {RATE_LIMIT_AUDITS} проверки за последние 2 минуты. "
                        f"Следующую проверку можно запустить через {_format_wait(retry_after)}."
                    ),
                    headers={"Retry-After": str(retry_after)},
                )

            attempts.append(now)
            job = Job(job_id=uuid.uuid4().hex, requested_url=requested_url, client_ip=client_ip)
            self.jobs[job.job_id] = job

        logger.info("Audit %s queued: ip=%s url=%s", job.job_id, client_ip, requested_url)
        asyncio.create_task(self._run(job))
        return job

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Audit job not found")
        return job

    async def _run(self, job: Job) -> None:
        queued_at = monotonic()
        async with self.audit_slots:
            queue_wait = monotonic() - queued_at
            try:
                async with job.lock:
                    job.status = "discovering"
                    job.started_at = utcnow()

                logger.info(
                    "Audit %s started after %.3fs queue wait: ip=%s url=%s",
                    job.job_id,
                    queue_wait,
                    job.client_ip,
                    job.requested_url,
                )

                async def execute_audit() -> None:
                    discovery_started = monotonic()
                    discovered = await discover_audit_urls(job.requested_url)
                    urls: list[str] = discovered["urls"]
                    logger.info(
                        "Audit %s discovery completed in %.3fs: %s pages, %s sitemap(s), ip=%s",
                        job.job_id,
                        monotonic() - discovery_started,
                        len(urls),
                        len(discovered["sitemap_urls"]),
                        job.client_ip,
                    )

                    async with job.lock:
                        job.normalized_url = discovered["normalized_url"]
                        job.robots_url = discovered["robots_url"]
                        job.robots_found = discovered["robots_found"]
                        job.robots_sitemap_urls = discovered["robots_sitemap_urls"]
                        job.sitemap_urls = discovered["sitemap_urls"]
                        job.discovered_urls = len(urls)
                        job.limited = discovered["limited"]
                        job.status = "running"

                    async def on_result(result: PageResult) -> None:
                        async with job.lock:
                            job.results.append(result)
                            job.checked_urls += 1
                            if result.check_failed:
                                job.failed_checks += 1
                            elif result.errors:
                                job.errors_found += 1
                            elif result.warnings:
                                job.warnings_found += 1

                    await run_pages(urls, on_result)
                    self._apply_meta_duplicate_warnings(job.results)

                await asyncio.wait_for(execute_audit(), timeout=AUDIT_TIMEOUT)

                async with job.lock:
                    job.status = "completed"
                    job.completed_at = utcnow()
                elapsed = (job.completed_at - job.started_at).total_seconds() if job.started_at and job.completed_at else 0.0
                logger.info(
                    "Audit %s completed in %.3fs: %s/%s pages, errors=%s, warnings=%s, ip=%s",
                    job.job_id,
                    elapsed,
                    job.checked_urls,
                    job.discovered_urls,
                    job.errors_found,
                    job.warnings_found,
                    job.client_ip,
                )

            except asyncio.TimeoutError:
                logger.error(
                    "Audit %s timed out after %.0f seconds: ip=%s url=%s",
                    job.job_id,
                    AUDIT_TIMEOUT,
                    job.client_ip,
                    job.requested_url,
                )
                async with job.lock:
                    job.status = "failed"
                    job.error = f"Проверка заняла больше {int(AUDIT_TIMEOUT)} секунд и была остановлена. Попробуйте ещё раз позже."
                    job.completed_at = utcnow()
            except HTTPException as exc:
                logger.warning("Audit %s failed: %s, ip=%s", job.job_id, exc.detail, job.client_ip)
                async with job.lock:
                    job.status = "failed"
                    job.error = str(exc.detail)
                    job.completed_at = utcnow()
            except Exception:
                logger.exception("Audit %s failed with unexpected error, ip=%s", job.job_id, job.client_ip)
                async with job.lock:
                    job.status = "failed"
                    job.error = "Audit failed"
                    job.completed_at = utcnow()

    @staticmethod
    def _apply_meta_duplicate_warnings(results: list[PageResult]) -> None:
        checks = (
            ("title", "Одинаковый <title> используется на нескольких страницах"),
            ("description", "Одинаковый meta description используется на нескольких страницах"),
            ("keywords", "Одинаковый meta keywords используется на нескольких страницах"),
        )

        for field_name, message in checks:
            groups: dict[str, list[PageResult]] = {}
            for result in results:
                value = getattr(result.meta, field_name, None)
                if not value:
                    continue
                normalized = " ".join(value.split()).casefold()
                groups.setdefault(normalized, []).append(result)

            for matches in groups.values():
                if len(matches) < 2:
                    continue
                warning = f"{message}: {len(matches)} страниц"
                for result in matches:
                    if warning not in result.meta.warnings:
                        result.meta.warnings.append(warning)

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
            robots_sitemap_urls=job.robots_sitemap_urls,
            sitemap_urls=job.sitemap_urls,
            discovered_urls=job.discovered_urls,
            checked_urls=job.checked_urls,
            max_urls=MAX_AUDIT_URLS,
            limited=job.limited,
            progress_percent=progress,
            errors_found=job.errors_found,
            warnings_found=job.warnings_found,
            failed_checks=job.failed_checks,
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
            failed_checks=job.failed_checks,
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

            # Keep the in-memory rate-limit map small on a long-running service.
            now = monotonic()
            rate_cutoff = now - RATE_LIMIT_WINDOW_SECONDS
            empty_ips: list[str] = []
            for ip, attempts in self._rate_attempts.items():
                while attempts and attempts[0] <= rate_cutoff:
                    attempts.popleft()
                if not attempts:
                    empty_ips.append(ip)
            for ip in empty_ips:
                self._rate_attempts.pop(ip, None)


job_manager = JobManager()

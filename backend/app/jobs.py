from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import monotonic
from urllib.parse import urlsplit
import uuid

from fastapi import HTTPException

from .audit import ACCESS_BLOCKED_STATUS_CODES, discover_audit_urls, run_pages
from .canonical_resolve import resolve_canonicals
from .config import (
    AUDIT_TIMEOUT,
    DOMAIN_COOLDOWN_SECONDS,
    JOB_TTL_SECONDS,
    MAX_AUDIT_URLS,
    MAX_CONCURRENT_AUDITS,
    MAX_JOBS,
    MAX_QUEUED_AUDITS,
    RATE_LIMIT_AUDITS,
    RATE_LIMIT_WINDOW_SECONDS,
)
from .models import AuditJobStatus, AuditResultsResponse, JobStatus, PageResult
from .security import normalize_public_url


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


# --- Conservative mid-audit "site started blocking us" heuristic (see
# JobManager._make_on_result below) --------------------------------------
#
# A single 401/403/429 on one page is not evidence the whole site is
# blocking HeadInspect - some pages are legitimately restricted. But a WAF
# or anti-bot system that kicks in mid-crawl typically does so abruptly: a
# long run of normal responses is suddenly followed by a dense, sustained
# run of 401/403/429 responses. We only act on the latter shape:
#   - at least BLOCK_DETECT_MIN_GOOD_BEFORE pages must already have been
#     checked *without* being blocked (proves the site was letting us
#     through, so this isn't just "this site 403s everything"), and
#   - at least BLOCK_DETECT_WINDOW of the most recent results must be
#     401/403/429 at a rate of at least BLOCK_DETECT_RATIO.
BLOCK_DETECT_WINDOW = 12
BLOCK_DETECT_MIN_GOOD_BEFORE = 5
BLOCK_DETECT_RATIO = 0.9
# Reuse audit.py's set rather than maintaining a second copy of the same
# three status codes - they must never silently drift apart.
BLOCK_DETECT_STATUS_CODES = ACCESS_BLOCKED_STATUS_CODES


# --- Domain cooldown (see JobManager._cooldown_site_key / JobManager.create)
#
# Semantics, decided up front so the reasoning lives in one place:
#
# - **Site key**: the lowercased hostname from normalize_public_url() - the
#   same normalization already used to validate every audit URL - and
#   nothing else. Scheme (http/https), explicit default port, path, query
#   string and fragment are all ignored: none of them identify a
#   *different* site to audit, and letting any of them vary the key would
#   make the cooldown trivial to bypass by editing the URL. www/non-www are
#   deliberately kept as *separate* keys: HeadInspect never aliases them
#   from the URL string alone anywhere else in the codebase - the only
#   place it treats them as the same site is discover_audit_urls, and only
#   *after* actually following a same-site redirect on the entry page (see
#   tests/test_www_nonwww.py). Guessing that equivalence here, before any
#   request has been made, would be a new rule invented just for this
#   feature, not the existing one.
# - **Starts at job creation** (i.e. the moment the job is accepted into
#   the queue), not at the start of execution or at completion. The heavy
#   cost this protects against - real HTTP requests hitting the audited
#   site - begins essentially as soon as the job is accepted (queue wait is
#   bounded by MAX_QUEUED_AUDITS/MAX_CONCURRENT_AUDITS and is normally
#   short), and recording it at creation, in the same place and using the
#   same `now` as RATE_LIMIT_AUDITS's per-IP bookkeeping, needs no
#   additional coordination with the background `_run` task.
# - **Applies regardless of outcome.** A job that ends up `failed` or
#   `completed_partial` still made real requests against the site before
#   that happened (DNS + entry fetch, and often much more) - the cooldown
#   is not lifted or shortened for those, only for one reason: it was
#   never armed in the first place, because the URL failed even basic
#   normalization (see _cooldown_site_key) and so never reached the site.


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
    sitemap_issues: list[str] = field(default_factory=list)
    discovered_urls: int = 0
    checked_urls: int = 0
    limited: bool = False
    errors_found: int = 0
    warnings_found: int = 0
    failed_checks: int = 0
    access_blocked_status: int | None = None
    blocked_mid_audit: bool = False
    mid_audit_block_status: int | None = None
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    partial_reason: str | None = None
    results: list[PageResult] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.audit_slots = asyncio.Semaphore(MAX_CONCURRENT_AUDITS)
        self._cleanup_lock = asyncio.Lock()
        self._create_lock = asyncio.Lock()
        self._rate_attempts: dict[str, deque[float]] = defaultdict(deque)
        # site key (see _cooldown_site_key) -> monotonic time the most
        # recent audit of that site was created.
        self._domain_cooldowns: dict[str, float] = {}

    @staticmethod
    def _cooldown_site_key(requested_url: str) -> str | None:
        """The key domain cooldown is tracked under - see the module-level
        comment above BLOCK_DETECT_STATUS_CODES for the full reasoning.
        Returns None if the URL doesn't even pass basic normalization; such
        a URL is not blocked by cooldown here, it simply fails later, same
        as it already does today once the job actually runs.
        """
        try:
            normalized = normalize_public_url(requested_url)
        except HTTPException:
            return None
        return urlsplit(normalized).hostname

    async def create(self, requested_url: str, *, client_ip: str) -> Job:
        await self.cleanup()

        async with self._create_lock:
            if len(self.jobs) >= MAX_JOBS:
                # cleanup() just above only evicts while strictly *over*
                # MAX_JOBS (see its MAX_JOBS handling) - sitting exactly
                # *at* the cap is deliberately left alone there, since
                # cleanup() on its own has no reason to assume a new job is
                # about to be added. Here we DO know that, so make room for
                # exactly the one job this call is about to create: evict
                # the single oldest safely-finished job (same criterion as
                # cleanup() - completed_at is not None - so queued/running/
                # discovering are never candidates), if one exists. Only if
                # every one of MAX_JOBS jobs is still active (not reachable
                # today: MAX_QUEUED_AUDITS + MAX_CONCURRENT_AUDITS is far
                # below MAX_JOBS) does this fall through to the 503 below -
                # never unbounded growth, never a crash, never touches an
                # active job.
                oldest_finished = min(
                    (job for job in self.jobs.values() if job.completed_at is not None),
                    key=lambda job: job.completed_at,
                    default=None,
                )
                if oldest_finished is not None:
                    self.jobs.pop(oldest_finished.job_id, None)
                else:
                    logger.warning(
                        "Audit rejected: MAX_JOBS reached (%s/%s), ip=%s, url=%s",
                        len(self.jobs),
                        MAX_JOBS,
                        client_ip,
                        requested_url,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail="Сервис сейчас занят: достигнут предел одновременно хранимых проверок. Попробуйте через несколько минут.",
                        headers={"Retry-After": "60"},
                    )

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

            site_key = self._cooldown_site_key(requested_url)
            if site_key is not None:
                last_started = self._domain_cooldowns.get(site_key)
                if last_started is not None:
                    site_elapsed = now - last_started
                    if site_elapsed < DOMAIN_COOLDOWN_SECONDS:
                        site_retry_after = max(1, int(DOMAIN_COOLDOWN_SECONDS - site_elapsed + 0.999))
                        logger.warning(
                            "Audit rejected: domain cooldown active (%s), retry_after=%ss, ip=%s, url=%s",
                            site_key,
                            site_retry_after,
                            client_ip,
                            requested_url,
                        )
                        raise HTTPException(
                            status_code=429,
                            detail=(
                                "Этот сайт уже проверялся недавно. Полный аудит одного и того же сайта "
                                f"можно запускать не чаще, чем раз в {_format_wait(DOMAIN_COOLDOWN_SECONDS)}. "
                                f"Следующую проверку этого сайта можно запустить через {_format_wait(site_retry_after)}."
                            ),
                            headers={"Retry-After": str(site_retry_after)},
                        )

            attempts.append(now)
            if site_key is not None:
                self._domain_cooldowns[site_key] = now
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

    @staticmethod
    def _make_on_result(job: Job, stop_event: asyncio.Event):
        """Build the on_result callback for a single job run.

        Bundles normal result bookkeeping with the mid-audit block heuristic
        described above the module-level constants. State (recent_outcomes,
        good_checks_seen, block_triggered) is private to this one job run.
        """
        recent_outcomes: deque[bool] = deque(maxlen=BLOCK_DETECT_WINDOW)
        state = {"good_checks_seen": 0, "block_triggered": False}

        async def on_result(result: PageResult) -> None:
            async with job.lock:
                # NOTE: this must key off status_code alone, never
                # result.check_failed. Since access_blocked pages (see
                # audit.py::analyze_page) are now ALSO check_failed=True,
                # `not result.check_failed` would silently exclude every
                # single 401/403/429 response from this detector - the exact
                # mass-block signal it exists to catch. status_code is still
                # populated for access_blocked pages (only genuine
                # network/timeout failures leave it as None, and `None in
                # BLOCK_DETECT_STATUS_CODES` is already False), so this is a
                # pure simplification, not a behavior change for those cases.
                is_block_response = result.status_code in BLOCK_DETECT_STATUS_CODES

                if not state["block_triggered"]:
                    recent_outcomes.append(is_block_response)
                    if not is_block_response:
                        state["good_checks_seen"] += 1

                    if (
                        state["good_checks_seen"] >= BLOCK_DETECT_MIN_GOOD_BEFORE
                        and len(recent_outcomes) == BLOCK_DETECT_WINDOW
                        and sum(recent_outcomes) / BLOCK_DETECT_WINDOW >= BLOCK_DETECT_RATIO
                    ):
                        state["block_triggered"] = True
                        job.blocked_mid_audit = True
                        job.mid_audit_block_status = result.status_code
                        logger.warning(
                            "Audit %s: site appears to have started blocking HeadInspect "
                            "mid-audit (HTTP %s), stopping further requests after %s checked pages, ip=%s",
                            job.job_id,
                            result.status_code,
                            job.checked_urls,
                            job.client_ip,
                        )
                        stop_event.set()

                if state["block_triggered"] and is_block_response:
                    # Part of the block storm itself, not a real page error:
                    # count it as an unavailable check, but do not let it
                    # inflate errors_found or show up as a normal page result.
                    job.failed_checks += 1
                    return

                job.results.append(result)
                job.checked_urls += 1
                if result.check_failed:
                    job.failed_checks += 1
                elif result.errors:
                    job.errors_found += 1
                elif result.warnings:
                    job.warnings_found += 1

        return on_result

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
                    access_blocked_status = discovered.get("access_blocked_status")
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
                        job.sitemap_issues = discovered.get("sitemap_issues", [])
                        job.discovered_urls = len(urls)
                        job.limited = discovered["limited"]
                        job.access_blocked_status = access_blocked_status
                        job.status = "running"

                    if access_blocked_status is not None:
                        logger.warning(
                            "Audit %s stopped at entry URL: HTTP %s blocks automated access, ip=%s url=%s",
                            job.job_id,
                            access_blocked_status,
                            job.client_ip,
                            job.requested_url,
                        )
                        return

                    stop_event = asyncio.Event()
                    on_result = self._make_on_result(job, stop_event)

                    await run_pages(urls, on_result, stop_event=stop_event)
                    self._apply_meta_duplicate_warnings(job.results)
                    # Cross-page canonical resolution: synchronous, in-memory,
                    # never any network. Covers normal completion and the
                    # blocked-mid-audit partial (run_pages returns normally in
                    # both). The AUDIT_TIMEOUT partial path calls it separately
                    # in the TimeoutError handler below, on whatever we have.
                    resolve_canonicals(job.results)

                await asyncio.wait_for(execute_audit(), timeout=AUDIT_TIMEOUT)

                async with job.lock:
                    if job.blocked_mid_audit:
                        job.status = "completed_partial"
                        job.partial_reason = (
                            "Сайт начал ограничивать автоматические запросы HeadInspect во время "
                            f"проверки (сервер стал отвечать HTTP {job.mid_audit_block_status}). "
                            f"Показаны результаты {job.checked_urls} страниц, проверенных до начала "
                            "ограничения; остальные страницы не проверялись, чтобы не создавать лишнюю "
                            "нагрузку на сайт."
                        )
                    else:
                        job.status = "completed"
                    job.completed_at = utcnow()
                elapsed = (job.completed_at - job.started_at).total_seconds() if job.started_at and job.completed_at else 0.0
                logger.info(
                    "Audit %s completed in %.3fs: %s/%s pages, errors=%s, warnings=%s, blocked_mid_audit=%s, ip=%s",
                    job.job_id,
                    elapsed,
                    job.checked_urls,
                    job.discovered_urls,
                    job.errors_found,
                    job.warnings_found,
                    job.blocked_mid_audit,
                    job.client_ip,
                )

            except asyncio.TimeoutError:
                logger.error(
                    "Audit %s timed out after %.0f seconds: ip=%s url=%s, checked=%s",
                    job.job_id,
                    AUDIT_TIMEOUT,
                    job.client_ip,
                    job.requested_url,
                    job.checked_urls,
                )
                async with job.lock:
                    if job.checked_urls > 0:
                        # We already have usable data for some pages - do not
                        # discard it. Report what we have as a partial result
                        # instead of a bare failure. Resolve canonicals over
                        # the pages we did collect (a target missing from this
                        # partial map is simply "not checked", never an error).
                        resolve_canonicals(job.results)
                        job.status = "completed_partial"
                        if job.blocked_mid_audit:
                            job.partial_reason = (
                                "Сайт начал ограничивать автоматические запросы HeadInspect во время "
                                f"проверки (сервер стал отвечать HTTP {job.mid_audit_block_status}), а "
                                f"затем истёк общий лимит времени проверки. Показаны результаты "
                                f"{job.checked_urls} страниц из {job.discovered_urls} найденных."
                            )
                        else:
                            job.partial_reason = (
                                f"Проверка заняла больше {int(AUDIT_TIMEOUT)} секунд и была остановлена "
                                "до завершения. Показаны результаты "
                                f"{job.checked_urls} страниц из {job.discovered_urls} найденных."
                            )
                    else:
                        job.status = "failed"
                        job.error = (
                            f"Проверка заняла больше {int(AUDIT_TIMEOUT)} секунд и была остановлена. "
                            "Попробуйте ещё раз позже."
                        )
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
                # Не считаем редиректный URL отдельной страницей при поиске дублей.
                # Его содержимое относится к конечному URL и иначе создаёт ложные
                # дубли title/description/keywords. Сам редирект показывает Sitemap.
                if result.check_failed or (
                    result.requested_url
                    and result.url
                    and result.requested_url != result.url
                ):
                    continue

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
        elif job.status == "completed_partial":
            # Genuinely incomplete: show the real checked/discovered ratio
            # rather than claiming 100%, but don't get stuck below the
            # displayed maximum for an in-progress-looking bar either.
            progress = min(99, round(job.checked_urls / total * 100)) if total > 0 else 100
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
            sitemap_issues=job.sitemap_issues,
            discovered_urls=job.discovered_urls,
            checked_urls=job.checked_urls,
            max_urls=MAX_AUDIT_URLS,
            limited=job.limited,
            progress_percent=progress,
            errors_found=job.errors_found,
            warnings_found=job.warnings_found,
            failed_checks=job.failed_checks,
            access_blocked_status=job.access_blocked_status,
            blocked_mid_audit=job.blocked_mid_audit,
            mid_audit_block_status=job.mid_audit_block_status,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            error=job.error,
            partial_reason=job.partial_reason,
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

            # MAX_JOBS: same "safely finished" criterion as TTL above
            # (completed_at is not None - queued/running/discovering jobs
            # are never candidates), oldest-finished-first, evicting only as
            # many as needed to get back under the cap. This runs *after*
            # the TTL pass above, so a job that's both over MAX_JOBS and
            # past its TTL is simply already gone by this point - no double
            # bookkeeping. Deliberately only fires while strictly *over* the
            # cap - sitting exactly *at* MAX_JOBS is left alone here, since
            # cleanup() has no reason to assume a new job is about to be
            # added; create()'s own backstop (see above) makes room for
            # exactly one more job when it's the one asking. If everything
            # left is still active, nothing is evicted here (not reachable
            # today - see MAX_JOBS in config.py); create()'s backstop still
            # caps the dict regardless.
            if len(self.jobs) > MAX_JOBS:
                finished = sorted(
                    (job for job in self.jobs.values() if job.completed_at is not None),
                    key=lambda job: job.completed_at,
                )
                overflow = len(self.jobs) - MAX_JOBS
                for job in finished[:overflow]:
                    self.jobs.pop(job.job_id, None)

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

            # Same idea for the domain-cooldown map: an entry is only ever
            # useful while its cooldown window hasn't expired yet.
            cooldown_cutoff = now - DOMAIN_COOLDOWN_SECONDS
            expired_sites = [
                site
                for site, started in self._domain_cooldowns.items()
                if started <= cooldown_cutoff
            ]
            for site in expired_sites:
                self._domain_cooldowns.pop(site, None)


job_manager = JobManager()

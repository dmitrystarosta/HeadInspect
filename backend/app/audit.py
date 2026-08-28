from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import HTTPException

from .analyzers.meta import analyze_meta
from .analyzers.open_graph import analyze_open_graph
from .analyzers.schema import analyze_schema
from .config import MAX_HTML_BYTES, PAGE_CONCURRENCY, PAGE_TIMEOUT
from .fetcher import safe_fetch
from .htmlmeta import MetadataParser
from .models import PageResult
from .robots import fetch_robots, sitemap_urls_from_robots
from .security import validate_public_url
from .sitemap import discover_urls


logger = logging.getLogger("uvicorn.error")


ProgressCallback = Callable[[PageResult], Awaitable[None]]


async def analyze_page(
    url: str,
    semaphore: asyncio.Semaphore,
    *,
    stop_event: asyncio.Event | None = None,
) -> PageResult | None:
    async with semaphore:
        # Re-check *after* acquiring the semaphore, not only before. With a
        # large URL list, every worker's pre-semaphore stop_event check (see
        # run_pages.worker) can run before a single real fetch has
        # completed - detection only fires after dozens of genuine
        # responses come back, which takes real wall-clock time. Without
        # this second check, hundreds of workers that already passed the
        # first check end up merely queued on the semaphore, and each one
        # still makes a real request once its turn comes, long after the
        # site was already confirmed to be blocking HeadInspect. Returning
        # None here means "never attempted" - the caller must not count it
        # anywhere (not checked_urls, not failed_checks, not results).
        if stop_event is not None and stop_event.is_set():
            return None

        errors: list[str] = []
        warnings: list[str] = []

        try:
            result = await safe_fetch(
                url,
                max_bytes=MAX_HTML_BYTES,
                accepted_content_types=("text/html", "application/xhtml+xml"),
            )
        except HTTPException as exc:
            return PageResult(
                url=url,
                requested_url=url,
                status_code=None,
                check_failed=True,
                check_error=f"Не удалось проверить страницу: {exc.detail}",
            )

        if result.status_code >= 400:
            errors.append(f"HTTP {result.status_code}")
        elif result.status_code >= 300:
            warnings.append(f"HTTP {result.status_code}")

        parser = MetadataParser()
        try:
            parser.feed(result.content.decode("utf-8", errors="replace"))
        except Exception:
            errors.append("Не удалось разобрать HTML")

        og_data, og_errors, og_warnings = await analyze_open_graph(parser.og, result.url)
        errors.extend(og_errors)
        warnings.extend(og_warnings)

        meta_data = analyze_meta(parser)
        schema_data = analyze_schema(parser.json_ld_blocks, parser.microdata_types)

        return PageResult(
            url=result.url,
            requested_url=url,
            status_code=result.status_code,
            title=parser.title,
            meta_description=parser.meta_description,
            open_graph=og_data,
            meta=meta_data,
            schema_data=schema_data,
            errors=errors,
            warnings=warnings,
        )


async def discover_audit_urls(raw_url: str) -> dict:
    normalized = await validate_public_url(raw_url)

    # Сначала проверяем сам введённый URL. Если сайт уже на входе запрещает
    # автоматический доступ, дальнейший обход robots/sitemap даст недостоверный
    # результат и только создаст лишнюю нагрузку.
    try:
        entry = await safe_fetch(
            normalized,
            max_bytes=MAX_HTML_BYTES,
            accepted_content_types=("text/html", "application/xhtml+xml"),
        )
    except HTTPException:
        entry = None

    if entry is not None and entry.status_code in (401, 403, 429):
        return {
            "normalized_url": entry.url,
            "robots_url": None,
            "robots_found": None,
            "robots_sitemap_urls": [],
            "sitemap_urls": [],
            "sitemap_issues": [],
            "urls": [],
            "limited": False,
            "access_blocked_status": entry.status_code,
        }

    robots_url, robots_found, robots_text = await fetch_robots(normalized)
    robots_sitemaps = sitemap_urls_from_robots(robots_text) if robots_found else []

    # Sitemap URLs are matched against the site's *actual* host, not
    # necessarily the exact host the user typed: if the entry page redirects
    # example.ru -> www.example.ru (or vice versa) over an ordinary HTTP
    # redirect, www.example.ru is the site's real host and sitemap entries on
    # that host must not be discarded as "a different site". This only ever
    # trusts a host that the entry page itself redirected to (already
    # revalidated by safe_fetch/resolve_and_validate_host on every hop) - it
    # does not allow arbitrary same-eTLD+1 subdomains such as shop./forum./
    # admin.example.ru, which are genuinely different sites.
    effective_host = urlsplit(entry.url).hostname if entry is not None else urlsplit(normalized).hostname

    urls, processed_sitemaps, limited, sitemap_issues = await discover_urls(
        normalized,
        robots_sitemaps,
        site_host=effective_host,
    )

    if not urls:
        urls = [normalized]

    return {
        "normalized_url": normalized,
        "robots_url": robots_url,
        "robots_found": robots_found,
        "robots_sitemap_urls": robots_sitemaps,
        "sitemap_urls": processed_sitemaps,
        "sitemap_issues": sitemap_issues,
        "urls": urls,
        "limited": limited,
        "access_blocked_status": None,
    }


async def run_pages(
    urls: list[str],
    on_result: ProgressCallback,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    semaphore = asyncio.Semaphore(PAGE_CONCURRENCY)

    async def worker(url: str) -> None:
        if stop_event is not None and stop_event.is_set():
            # Cheap early exit for workers whose turn comes after detection
            # already fired - avoids even queuing on the semaphore.
            return

        try:
            result = await asyncio.wait_for(
                analyze_page(url, semaphore, stop_event=stop_event),
                timeout=PAGE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("Page audit timed out after %.0fs: %s", PAGE_TIMEOUT, url)
            result = PageResult(
                url=url,
                requested_url=url,
                status_code=None,
                check_failed=True,
                check_error=f"Страница не ответила за {PAGE_TIMEOUT:.0f} с",
            )

        if result is None:
            # analyze_page declined to run at all (stop_event fired while
            # this worker was queued behind PAGE_CONCURRENCY) - genuinely
            # never attempted, must not be counted anywhere.
            return

        await on_result(result)

    await asyncio.gather(*(worker(url) for url in urls))

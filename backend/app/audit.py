from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import HTTPException

from .analyzers.canonical import analyze_canonical
from .analyzers.meta import analyze_meta
from .analyzers.open_graph import analyze_open_graph
from .analyzers.schema import analyze_schema
from .config import MAX_HTML_BYTES, PAGE_CONCURRENCY, PAGE_TIMEOUT
from .fetcher import safe_fetch
from .htmlmeta import MetadataParser, decode_html
from .models import PageResult
from .robots import fetch_robots, sitemap_urls_from_robots
from .security import validate_public_url
from .sitemap import discover_urls


logger = logging.getLogger("uvicorn.error")


ProgressCallback = Callable[[PageResult], Awaitable[None]]

# HTTP statuses that mean "the server is refusing/throttling automated
# access", not "here is the site's real content". A response in this set is
# still check_failed=True (Open Graph/Meta/Schema must not draw conclusions
# from it), but - unlike a genuine network/timeout failure - status_code IS
# known and kept on the result, so Sitemap (which cares about URL
# availability, not content trust) can keep treating it as a normal,
# informative HTTP status rather than "unavailable".
ACCESS_BLOCKED_STATUS_CODES = frozenset({401, 403, 429})

# Reused verbatim from common.js's renderAccessBlocked so a blocked *page*
# and a blocked *entry URL* explain themselves in the same voice.
ACCESS_BLOCKED_MESSAGES = {
    401: "Сервер требует авторизацию и не разрешил HeadInspect получить страницу.",
    403: "Сервер запретил HeadInspect автоматический доступ. При этом страница может открываться в обычном браузере.",
    429: "Сервер ограничил частоту автоматических запросов HeadInspect.",
}


def _classify_fetch_failure_reason(exc: HTTPException) -> str:
    """Turn safe_fetch's free-text HTTPException into one of the stable
    check_reason values, once, here - so no frontend has to pattern-match
    check_error text to know why a page couldn't be checked (item 5)."""
    detail = (exc.detail or "").lower()
    if exc.status_code == 504 or "timeout" in detail:
        return "timeout"
    if "content type" in detail or "too large" in detail:
        return "content_type"
    return "network"


async def analyze_page(
    url: str,
    semaphore: asyncio.Semaphore,
    *,
    stop_event: asyncio.Event | None = None,
) -> PageResult | None:
    if stop_event is not None and stop_event.is_set():
        # Cheap early exit for workers whose turn comes after mid-audit
        # block detection already fired - avoids even queuing on the
        # semaphore for a site we've already decided to stop hammering.
        return None

    async with semaphore:
        # Re-check *after* acquiring the semaphore, not only before. With a
        # large URL list, every worker's pre-semaphore stop_event check can
        # run before a single real fetch has completed - detection only
        # fires after dozens of genuine responses come back, which takes
        # real wall-clock time. Without this second check, hundreds of
        # workers that already passed the first check end up merely queued
        # on the semaphore, and each one still makes a real request once its
        # turn comes, long after the site was already confirmed to be
        # blocking HeadInspect. Returning None here means "never attempted"
        # - the caller must not count it anywhere (not checked_urls, not
        # failed_checks, not results).
        if stop_event is not None and stop_event.is_set():
            return None

        # PAGE_TIMEOUT is deliberately applied *here* - after the semaphore
        # has actually been acquired - and not around this whole function.
        # Wrapping the semaphore wait in the same timeout used to mean a URL
        # could be reported as "did not respond in 30s" purely because it
        # spent all 30 of those seconds queued behind PAGE_CONCURRENCY,
        # never once touching the network. With up to MAX_AUDIT_URLS (500)
        # workers all created by asyncio.gather at nearly the same instant,
        # every one of them would start its own 30-second countdown at
        # essentially the same wall-clock moment - the deadline had nothing
        # to do with how long *this page's own* check actually took. Timing
        # only the real fetch-and-analyze work below means "не ответила за
        # 30 с" now means exactly that: this page's own check ran for 30
        # seconds after it started and did not finish.
        try:
            return await asyncio.wait_for(_fetch_and_analyze(url), timeout=PAGE_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Page audit timed out after %.0fs: %s", PAGE_TIMEOUT, url)
            return PageResult(
                url=url,
                requested_url=url,
                status_code=None,
                check_failed=True,
                check_reason="timeout",
                check_error=f"Страница не ответила за {PAGE_TIMEOUT:.0f} с",
            )


async def _fetch_and_analyze(url: str) -> PageResult:
    """The actual per-page work: fetch, decide access_blocked/ordinary, run
    the content analyzers. Split out from analyze_page so PAGE_TIMEOUT can
    wrap *only* this - not the semaphore wait that precedes it."""
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
            check_reason=_classify_fetch_failure_reason(exc),
            check_error=f"Не удалось проверить страницу: {exc.detail}",
        )

    if result.status_code in ACCESS_BLOCKED_STATUS_CODES:
        # A response WAS received, but it's very unlikely to be the
        # site's real page - typically a WAF/anti-bot "verification"
        # challenge. Deliberately do NOT run the content analyzers on
        # it: any "Нет og:title"/"Нет meta description"/etc. we'd
        # produce would describe the *block page*, not the real site,
        # and would be indistinguishable from a genuine SEO problem to
        # someone reading the results. Still worth a light parse for the
        # page's own <title> (e.g. "Verification required") purely as a
        # diagnostic detail for the person reading the report - this is
        # NOT used to classify the page, only to make check_error more
        # informative when the server happens to send one.
        blocked_title: str | None = None
        try:
            parser = MetadataParser()
            parser.feed(decode_html(result.content, result.headers.get("content-type")))
            blocked_title = parser.title
        except Exception:
            blocked_title = None

        explanation = ACCESS_BLOCKED_MESSAGES.get(
            result.status_code, "Сервер ограничил автоматический доступ HeadInspect к этой странице."
        )
        check_error = f"HTTP {result.status_code}. {explanation}"
        if blocked_title:
            check_error += f' Заголовок страницы, которую вернул сервер: «{blocked_title}».'

        return PageResult(
            url=result.url,
            requested_url=url,
            status_code=result.status_code,
            check_failed=True,
            check_reason="access_blocked",
            check_error=check_error,
            title=blocked_title,
        )

    if result.status_code >= 400:
        errors.append(f"HTTP {result.status_code}")
    elif result.status_code >= 300:
        warnings.append(f"HTTP {result.status_code}")

    parser = MetadataParser()
    try:
        parser.feed(decode_html(result.content, result.headers.get("content-type")))
    except Exception:
        errors.append("Не удалось разобрать HTML")

    og_data, og_errors, og_warnings = await analyze_open_graph(parser.og, result.url)
    errors.extend(og_errors)
    warnings.extend(og_warnings)

    meta_data = analyze_meta(parser)
    schema_data = analyze_schema(parser.json_ld_blocks, parser.microdata_types)
    canonical_data = analyze_canonical(parser, result.headers, url, result.url)

    return PageResult(
        url=result.url,
        requested_url=url,
        status_code=result.status_code,
        title=parser.title,
        meta_description=parser.meta_description,
        open_graph=og_data,
        meta=meta_data,
        schema_data=schema_data,
        canonical=canonical_data,
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
            "sitemap_declared_unfetched": False,
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

    # Discovery is "degraded" only in the specific case the user must be warned
    # about: a sitemap was explicitly declared in robots.txt, it could not be
    # fetched/parsed (so `sitemap_issues` is non-empty), and as a result no
    # pages were discovered at all - discovery therefore falls back to auditing
    # just the entry page below. This deliberately does NOT fire when there was
    # no declared sitemap (small sites, guessed-default misses) or when a
    # sitemap was fetched fine and simply contained a single page (in that case
    # `urls` is non-empty here and there are no issues).
    sitemap_declared_unfetched = bool(robots_sitemaps) and not urls and bool(sitemap_issues)

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
        "sitemap_declared_unfetched": sitemap_declared_unfetched,
    }


async def run_pages(
    urls: list[str],
    on_result: ProgressCallback,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    semaphore = asyncio.Semaphore(PAGE_CONCURRENCY)

    async def worker(url: str) -> None:
        # analyze_page now owns the stop_event checks (before and after
        # acquiring the semaphore) and the PAGE_TIMEOUT wrapping (applied
        # only to the real fetch-and-analyze work, not to queueing) - see
        # its docstring/comments. worker() is intentionally just dispatch:
        # None means "never attempted, don't count it anywhere".
        result = await analyze_page(url, semaphore, stop_event=stop_event)
        if result is None:
            return
        await on_result(result)

    await asyncio.gather(*(worker(url) for url in urls))

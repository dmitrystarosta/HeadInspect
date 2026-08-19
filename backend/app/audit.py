from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import HTTPException

from .analyzers.open_graph import analyze_open_graph
from .config import MAX_HTML_BYTES, PAGE_CONCURRENCY
from .fetcher import safe_fetch
from .htmlmeta import MetadataParser
from .models import PageResult
from .robots import fetch_robots, sitemap_urls_from_robots
from .security import validate_public_url
from .sitemap import discover_urls


ProgressCallback = Callable[[PageResult], Awaitable[None]]


async def analyze_page(url: str, semaphore: asyncio.Semaphore) -> PageResult:
    async with semaphore:
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
                status_code=None,
                errors=[f"Страница недоступна: {exc.detail}"],
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

        og_data, og_errors, og_warnings = analyze_open_graph(parser.og)
        errors.extend(og_errors)
        warnings.extend(og_warnings)

        return PageResult(
            url=result.url,
            status_code=result.status_code,
            title=parser.title,
            meta_description=parser.meta_description,
            open_graph=og_data,
            errors=errors,
            warnings=warnings,
        )


async def discover_audit_urls(raw_url: str) -> dict:
    normalized = await validate_public_url(raw_url)

    robots_url, robots_found, robots_text = await fetch_robots(normalized)
    robots_sitemaps = sitemap_urls_from_robots(robots_text) if robots_found else []

    urls, processed_sitemaps, limited = await discover_urls(normalized, robots_sitemaps)

    if not urls:
        urls = [normalized]

    return {
        "normalized_url": normalized,
        "robots_url": robots_url,
        "robots_found": robots_found,
        "sitemap_urls": processed_sitemaps,
        "urls": urls,
        "limited": limited,
    }


async def run_pages(
    urls: list[str],
    on_result: ProgressCallback,
) -> None:
    semaphore = asyncio.Semaphore(PAGE_CONCURRENCY)

    async def worker(url: str) -> None:
        result = await analyze_page(url, semaphore)
        await on_result(result)

    await asyncio.gather(*(worker(url) for url in urls))

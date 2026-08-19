from __future__ import annotations

from collections import deque
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from fastapi import HTTPException

from .config import MAX_AUDIT_URLS, MAX_SITEMAP_BYTES, MAX_SITEMAP_DEPTH, MAX_SITEMAPS
from .fetcher import safe_fetch
from .security import validate_public_url


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _parse_sitemap_xml(content: bytes) -> tuple[str, list[str]]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise HTTPException(status_code=502, detail="Invalid sitemap XML") from exc

    root_name = _local_name(root.tag)
    locs: list[str] = []

    for node in root.iter():
        if _local_name(node.tag) == "loc" and node.text:
            value = node.text.strip()
            if value:
                locs.append(value)

    if root_name == "sitemapindex":
        return "index", locs
    if root_name == "urlset":
        return "urlset", locs

    raise HTTPException(status_code=502, detail="Unsupported sitemap XML root element")


async def discover_urls(
    site_url: str,
    initial_sitemaps: list[str],
) -> tuple[list[str], list[str], bool]:
    if not initial_sitemaps:
        initial_sitemaps = [
            urljoin(site_url, "/sitemap.xml"),
            urljoin(site_url, "/sitemap_index.xml"),
        ]

    queue = deque((url, 0) for url in initial_sitemaps)
    seen_sitemaps: set[str] = set()
    processed_sitemaps: list[str] = []
    pages: list[str] = []
    seen_pages: set[str] = set()
    limited = False

    site_host = urlsplit(site_url).hostname

    while queue and len(seen_sitemaps) < MAX_SITEMAPS:
        sitemap_url, depth = queue.popleft()
        if depth > MAX_SITEMAP_DEPTH:
            continue

        try:
            sitemap_url = await validate_public_url(sitemap_url)
        except HTTPException:
            continue

        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)

        try:
            result = await safe_fetch(
                sitemap_url,
                max_bytes=MAX_SITEMAP_BYTES,
                accepted_content_types=("xml", "text/plain", "application/octet-stream"),
            )
        except HTTPException:
            continue

        if result.status_code != 200:
            continue

        try:
            kind, locs = _parse_sitemap_xml(result.content)
        except HTTPException:
            continue

        processed_sitemaps.append(result.url)

        if kind == "index":
            for child in locs:
                if len(seen_sitemaps) + len(queue) >= MAX_SITEMAPS:
                    break
                queue.append((child, depth + 1))
            continue

        for page_url in locs:
            try:
                normalized = await validate_public_url(page_url)
            except HTTPException:
                continue

            if urlsplit(normalized).hostname != site_host:
                continue

            if normalized in seen_pages:
                continue

            if len(pages) >= MAX_AUDIT_URLS:
                limited = True
                return pages, processed_sitemaps, limited

            seen_pages.add(normalized)
            pages.append(normalized)

    return pages, processed_sitemaps, limited

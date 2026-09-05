from __future__ import annotations

import gzip
from collections import deque
from io import BytesIO
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from fastapi import HTTPException

from .config import MAX_AUDIT_URLS, MAX_SITEMAP_BYTES, MAX_SITEMAP_DEPTH, MAX_SITEMAPS
from .fetcher import safe_fetch
from .security import validate_public_url


GZIP_MAGIC = b"\x1f\x8b"

# A gzip-compressed sitemap can already only reach us as at most
# MAX_SITEMAP_BYTES of compressed bytes (enforced by safe_fetch), but gzip
# can still compress a hostile payload far beyond that once decompressed
# ("gzip bomb"). Cap the decompressed size generously but firmly so a
# malicious/broken sitemap.xml.gz cannot exhaust memory.
MAX_SITEMAP_DECOMPRESSED_BYTES = MAX_SITEMAP_BYTES * 10


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _maybe_decompress_gzip(content: bytes) -> bytes:
    """Transparently decompress a gzip-compressed sitemap.

    Detection is based on the gzip magic bytes rather than the HTTP
    Content-Type header: real-world servers advertise sitemap.xml.gz under a
    variety of types (application/gzip, application/x-gzip, application/
    octet-stream, or even text/xml with a misconfigured proxy), so relying on
    Content-Type alone silently misses valid gzip sitemaps.
    """
    if not content[:2] == GZIP_MAGIC:
        return content

    try:
        with gzip.GzipFile(fileobj=BytesIO(content)) as gz:
            decompressed = gz.read(MAX_SITEMAP_DECOMPRESSED_BYTES + 1)
    except OSError as exc:
        # A real, explainable failure (corrupted archive, truncated stream,
        # not actually gzip despite the magic bytes) - must not be swallowed
        # into a silent fallback, per the incident this fixes.
        raise HTTPException(
            status_code=502,
            detail="Sitemap is gzip-compressed but could not be decompressed (corrupted archive)",
        ) from exc

    if len(decompressed) > MAX_SITEMAP_DECOMPRESSED_BYTES:
        raise HTTPException(status_code=502, detail="Sitemap gzip archive is too large after decompression")

    return decompressed


def _parse_sitemap_xml(content: bytes) -> tuple[str, list[str]]:
    content = _maybe_decompress_gzip(content)

    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise HTTPException(status_code=502, detail="Invalid sitemap XML") from exc

    root_name = _local_name(root.tag)
    if root_name not in {"sitemapindex", "urlset"}:
        raise HTTPException(status_code=502, detail="Unsupported sitemap XML root element")

    # Only a <loc> that is a *direct* child of a <url> (urlset) or <sitemap>
    # (sitemapindex) entry is a page/sitemap reference per sitemaps.org.
    # Extension namespaces such as <image:image><image:loc> or
    # <video:video><video:loc> nest their own <loc> one level deeper, under a
    # child element, so this never picks them up - image/video URLs must not
    # be treated as pages to audit.
    entry_name = "sitemap" if root_name == "sitemapindex" else "url"
    locs: list[str] = []
    for entry in root:
        if _local_name(entry.tag) != entry_name:
            continue
        for child in entry:
            if _local_name(child.tag) == "loc" and child.text:
                value = child.text.strip()
                if value:
                    locs.append(value)
                break  # sitemaps.org specifies exactly one <loc> per entry

    kind = "index" if root_name == "sitemapindex" else "urlset"
    return kind, locs


async def discover_urls(
    site_url: str,
    initial_sitemaps: list[str],
    *,
    site_host: str | None = None,
) -> tuple[list[str], list[str], bool, list[str]]:
    # A sitemap explicitly declared in robots.txt is "known": if we can't
    # fetch/parse it, that is a real, reportable problem. The guessed default
    # locations (/sitemap.xml, /sitemap_index.xml), used only when robots.txt
    # declared none, are NOT known: a miss there is the common, expected case
    # and stays silent. Known-ness propagates to the children of a known
    # sitemapindex.
    declared = bool(initial_sitemaps)
    if not initial_sitemaps:
        initial_sitemaps = [
            urljoin(site_url, "/sitemap.xml"),
            urljoin(site_url, "/sitemap_index.xml"),
        ]

    queue = deque((url, 0, declared) for url in initial_sitemaps)
    seen_sitemaps: set[str] = set()
    processed_sitemaps: list[str] = []
    pages: list[str] = []
    seen_pages: set[str] = set()
    limited = False
    issues: list[str] = []

    if site_host is None:
        site_host = urlsplit(site_url).hostname

    while queue and len(seen_sitemaps) < MAX_SITEMAPS:
        sitemap_url, depth, known = queue.popleft()
        if depth > MAX_SITEMAP_DEPTH:
            continue

        raw_sitemap_url = sitemap_url
        try:
            sitemap_url = await validate_public_url(sitemap_url)
        except HTTPException:
            # Not a valid/allowed URL at all - nothing was ever fetched. For a
            # guessed default this is silent; for a sitemap declared in
            # robots.txt it is a reportable problem.
            if known:
                issues.append(f"{raw_sitemap_url}: некорректный или недопустимый адрес sitemap из robots.txt")
            continue

        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)

        try:
            result = await safe_fetch(
                sitemap_url,
                max_bytes=MAX_SITEMAP_BYTES,
                accepted_content_types=("xml", "text/plain", "application/octet-stream", "gzip"),
            )
        except HTTPException as exc:
            # A sitemap candidate that couldn't even be fetched (404, DNS
            # error, timeout, connection error, etc.). For the *guessed*
            # default locations this is an extremely common, expected outcome -
            # reporting every miss would be noise, not signal - so it stays
            # silent. But a sitemap explicitly *declared in robots.txt* that we
            # could not retrieve is exactly the case that previously turned a
            # transient network failure into a silent "site has one page":
            # record it so discovery does not look successful.
            if known:
                issues.append(f"{sitemap_url}: не удалось получить sitemap ({exc.detail})")
            continue

        if result.status_code != 200:
            if known:
                issues.append(f"{sitemap_url}: sitemap недоступен (HTTP {result.status_code})")
            continue

        try:
            kind, locs = _parse_sitemap_xml(result.content)
        except HTTPException as exc:
            # Unlike the cases above, this sitemap URL *did* answer with a
            # 200 and a body - it exists, but HeadInspect could not parse it
            # (corrupted gzip, invalid XML, unsupported root element). This
            # must not fall back to auditing a single page silently.
            issues.append(f"{sitemap_url}: {exc.detail}")
            continue

        processed_sitemaps.append(result.url)

        if kind == "index":
            for child in locs:
                if len(seen_sitemaps) + len(queue) >= MAX_SITEMAPS:
                    break
                # A child of a known (robots-declared) index is itself known:
                # if it can't be fetched, that is still a real gap in the
                # declared discovery mechanism, not a guessed-default miss.
                queue.append((child, depth + 1, known))
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
                return pages, processed_sitemaps, limited, issues

            seen_pages.add(normalized)
            pages.append(normalized)

    return pages, processed_sitemaps, limited, issues

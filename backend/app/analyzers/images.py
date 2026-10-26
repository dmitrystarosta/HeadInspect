"""Images module: SEO analysis of a page's image markup, plus lightweight
availability checks of unique image URLs.

Deliberately does NOT download image bytes - no GET, not even as a fallback,
no Pillow, no hashing, nothing written to disk or memory. Markup (alt,
width/height, loading, srcset/<picture>, format guessed from the URL
extension) comes from the already-fetched HTML; availability (HTTP status,
Content-Type, Content-Length) comes from HTTP HEAD only, through the existing
safe_fetch (so SSRF/DNS-pinning is fully preserved). HEAD requests are
deduplicated per audit, capped in number and concurrency, and bounded by a
per-page time budget; any HEAD error, timeout or ambiguous answer is reported
as "не удалось определить" and never fails the page or the other modules.
"""
from __future__ import annotations

import asyncio
from time import monotonic
from urllib.parse import urlsplit

from fastapi import HTTPException

from ..config import (
    IMAGES_ALT_MAX_LENGTH,
    IMAGES_HEAD_CONCURRENCY,
    IMAGES_HEAD_TIMEOUT,
    IMAGES_MAX_PER_PAGE,
    IMAGES_MAX_UNIQUE_FETCH_PER_AUDIT,
    IMAGES_PAGE_BUDGET_SECONDS,
    IMAGES_WARN_IMAGE_BYTES,
)
from ..fetcher import safe_fetch
from ..models import ImageItem, ImagesData
from ..security import normalize_public_url, resolve_relative_url


# A HEAD never carries a body, so the global size cap is irrelevant here; pass
# a value large enough that safe_fetch's Content-Length guard never trips on a
# (legitimately large) image we are only measuring, not downloading.
_HEAD_MAX_BYTES = 1 << 40

_IMAGE_HEAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HeadInspectBot/0.5; +https://headinspect.ru/)",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}

# Status codes that mean "this server does not support HEAD", not "the image
# is broken" - reported as inconclusive rather than an error (see brief).
_HEAD_UNSUPPORTED = frozenset({405, 501})

_EXT_FORMATS = {
    "jpg": "JPEG", "jpeg": "JPEG", "jfif": "JPEG",
    "png": "PNG", "gif": "GIF", "webp": "WEBP", "avif": "AVIF",
    "svg": "SVG", "bmp": "BMP", "ico": "ICO", "tif": "TIFF", "tiff": "TIFF",
}


def _guess_format(url: str | None) -> str | None:
    if not url:
        return None
    path = urlsplit(url).path
    last = path.rsplit("/", 1)[-1]
    if "." not in last:
        return None
    return _EXT_FORMATS.get(last.rsplit(".", 1)[-1].lower())


# Known analytics / tracking-pixel endpoints. These are not content images, so
# they are excluded from the Images audit entirely - not counted, not
# HEAD-checked, never producing warnings or errors. Recognised by their host
# (and, for hosts that also serve real content, by path) - i.e. by purpose,
# NOT by a 1×1 size - so ordinary small, empty-alt or third-party images are
# never dropped just for being small. Matched on the resolved absolute URL, so
# it works however the pixel is written in the HTML (protocol-relative src,
# inside <noscript>, query strings, etc.).
_TRACKER_HOST_SUFFIXES = (
    "mc.yandex.ru", "mc.yandex.com", "mc.yandex.by", "mc.yandex.kz", "mc.yandex.com.tr",
    "google-analytics.com", "analytics.google.com", "googletagmanager.com",
    "doubleclick.net", "googleadservices.com", "googlesyndication.com",
    "connect.facebook.net", "top-fwz1.mail.ru", "top.mail.ru",
    "counter.yadro.ru", "ads.vk.com",
)
# General hosts that also serve real content - treated as a tracker only when
# the path marks a tracking endpoint.
_TRACKER_HOST_PATHS = (
    ("yandex.ru", "/watch/"),
    ("facebook.com", "/tr"),
    ("vk.com", "/rtrg"),
    ("vk.com", "/rtd"),
    ("ok.ru", "/dk"),
    ("google.com", "/pagead"),
    ("google.com", "/ads"),
)


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def _path_has_marker(path: str, marker: str) -> bool:
    """Match a path marker on a segment boundary, not as a loose prefix: a short
    marker like "/tr" (Meta Pixel) must match "/tr", "/tr/" or "/tr?…" but NOT
    "/train/photo.jpg" or "/trends". A marker that already ends with "/" is a
    boundary in itself."""
    if path == marker:
        return True
    if not path.startswith(marker):
        return False
    if marker.endswith("/"):
        return True
    rest = path[len(marker):]
    return rest[0] in "/?#;," if rest else True


def _is_tracking_pixel(url: str | None) -> bool:
    if not url:
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        return False
    for suffix in _TRACKER_HOST_SUFFIXES:
        if _host_matches(host, suffix):
            return True
    path = parts.path or ""
    for general_host, marker in _TRACKER_HOST_PATHS:
        if _host_matches(host, general_host) and _path_has_marker(path, marker):
            return True
    return False


def _parse_srcset(srcset: str) -> list[str]:
    """Extract the candidate URLs from a srcset value (the URL token of each
    comma-separated candidate, dropping the width/pixel descriptor)."""
    urls: list[str] = []
    for candidate in srcset.split(","):
        candidate = candidate.strip()
        if not candidate:
            continue
        urls.append(candidate.split()[0])
    return urls


class ImageAuditContext:
    """Per-audit shared state for image availability: one HEAD per unique URL
    across the whole audit (concurrent requests to the same URL share one
    task), a hard cap on unique HEADs, and a concurrency cap. Each HEAD still
    passes through the process-wide fetch semaphore inside safe_fetch, so the
    global load cap is never exceeded."""

    def __init__(self) -> None:
        self._cache: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(IMAGES_HEAD_CONCURRENCY)
        self._unique_count = 0

    async def availability(self, url: str) -> dict:
        async with self._lock:
            task = self._cache.get(url)
            if task is None:
                if self._unique_count >= IMAGES_MAX_UNIQUE_FETCH_PER_AUDIT:
                    return {"checked": False,
                            "note": "не проверено: превышен лимит проверок изображений за аудит"}
                self._unique_count += 1
                task = asyncio.ensure_future(self._head(url))
                self._cache[url] = task
        # Awaited outside the lock so concurrent callers for different URLs do
        # not serialise. Shielded so one caller's timeout/cancel cannot cancel
        # the shared task other callers are also awaiting.
        return await asyncio.shield(task)

    async def _head(self, url: str) -> dict:
        async with self._semaphore:
            try:
                result = await asyncio.wait_for(
                    safe_fetch(url, max_bytes=_HEAD_MAX_BYTES, method="HEAD",
                               request_headers=_IMAGE_HEAD_HEADERS),
                    timeout=IMAGES_HEAD_TIMEOUT,
                )
            except asyncio.TimeoutError:
                return {"checked": True, "note": "не удалось определить: HEAD не ответил вовремя"}
            except HTTPException as exc:
                # Connection refused / DNS / blocked by SSRF validation / other
                # transport error: ambiguous for availability, not a definite
                # broken image - report as inconclusive, never download a body.
                return {"checked": True, "note": f"не удалось определить: {exc.detail}"}
            return _classify_head(result)


def _classify_head(result) -> dict:
    status = result.status_code
    content_type = (result.headers.get("content-type", "").split(";", 1)[0].strip().lower() or None)
    raw_len = result.headers.get("content-length")
    size = int(raw_len) if raw_len and raw_len.isdigit() else None

    info: dict = {"checked": True, "status": status, "content_type": content_type, "bytes": size}

    if status in _HEAD_UNSUPPORTED:
        info["note"] = "не удалось определить: сервер не поддерживает HEAD"
        return info
    if status >= 400:
        info["accessible"] = False
        info["error"] = f"HTTP {status}"
        return info

    info["accessible"] = True
    warnings: list[str] = []
    if content_type and not content_type.startswith("image/"):
        warnings.append(f"Content-Type не является изображением: {content_type}")
    if size is not None and size > IMAGES_WARN_IMAGE_BYTES:
        warnings.append(f"Возможно тяжёлое изображение: {size / 1024 / 1024:.1f} МБ")
    elif size is None:
        info["note"] = "размер не сообщён сервером"
    info["warnings"] = warnings
    return info


def _apply_availability(item: ImageItem, info: dict) -> None:
    item.checked = bool(info.get("checked"))
    if "status" in info:
        item.status_code = info["status"]
    if "content_type" in info:
        item.content_type = info["content_type"]
    if "bytes" in info:
        item.bytes = info["bytes"]
    if info.get("accessible") is not None:
        item.accessible = info["accessible"]
    if info.get("note"):
        item.availability_note = info["note"]
    if info.get("error"):
        item.errors.append(info["error"])
    for warning in info.get("warnings", []):
        item.warnings.append(warning)


async def _check_availability(items: list[ImageItem], ctx: ImageAuditContext) -> None:
    """HEAD-check each item's URL under a single per-page wall-clock budget.
    Items not resolved within the budget are marked inconclusive, never
    errored - a slow image host must not fail the page."""
    async def one(item: ImageItem) -> None:
        info = await ctx.availability(item.src)
        _apply_availability(item, info)

    try:
        await asyncio.wait_for(
            asyncio.gather(*(one(item) for item in items)),
            timeout=IMAGES_PAGE_BUDGET_SECONDS,
        )
    except asyncio.TimeoutError:
        for item in items:
            if not item.checked and item.availability_note is None:
                item.availability_note = "не проверено: лимит времени страницы"


async def analyze_images(parser, page_url: str, ctx: ImageAuditContext | None) -> ImagesData:
    data = ImagesData()

    def resolve(raw: str) -> str | None:
        if not raw:
            return None
        try:
            return normalize_public_url(resolve_relative_url(page_url, raw))
        except HTTPException:
            return None

    # Analytics/tracking pixels are not content images - drop them up front so
    # they never enter the count, availability checks, warnings or errors.
    records = [rec for rec in parser.images if not _is_tracking_pixel(resolve(rec["src"]))]
    data.total = len(records)

    items_by_url: dict[str, ImageItem] = {}
    order: list[ImageItem] = []

    def add_candidate(raw: str, url: str | None, source: str) -> ImageItem | None:
        if url is not None:
            existing = items_by_url.get(url)
            if existing is not None:
                return existing
        item = ImageItem(
            src=url, raw_src=raw or None, source=source,
            format_guess=_guess_format(url),
        )
        if url is not None:
            items_by_url[url] = item
        order.append(item)
        return item

    for idx, rec in enumerate(records[:IMAGES_MAX_PER_PAGE]):
        raw = rec["src"]
        url = resolve(raw)
        item = add_candidate(raw, url, "img")

        # Markup lives on the <img>, so set it on the (possibly shared) item.
        has_alt = rec["alt"] is not None
        alt_text = rec["alt"].strip() if has_alt else None
        alt_empty = has_alt and not alt_text
        item.has_alt = item.has_alt or has_alt
        if alt_text and not item.alt:
            item.alt = alt_text
        # Decorative (empty alt) only while no non-empty alt was seen for this URL.
        if not item.alt:
            item.alt_empty = item.alt_empty or alt_empty
        if rec["width"] is not None:
            item.width_attr = item.width_attr or rec["width"]
        if rec["height"] is not None:
            item.height_attr = item.height_attr or rec["height"]
        if rec["loading"]:
            item.loading = item.loading or rec["loading"]

        warnings: list[str] = []
        if not has_alt:
            warnings.append("нет атрибута alt")
        elif alt_empty:
            # Empty alt is valid for decorative images - no warning.
            pass
        elif len(alt_text) > IMAGES_ALT_MAX_LENGTH:
            warnings.append(f"слишком длинный alt ({len(alt_text)} симв.)")
        if rec["width"] is None or rec["height"] is None:
            warnings.append("не заданы атрибуты width/height (возможен сдвиг макета, если размеры не заданы в CSS)")
        if idx == 0 and rec["loading"] == "lazy":
            warnings.append("lazy-loading у первого изображения (возможно важное)")
        for w in warnings:
            if w not in item.warnings:
                item.warnings.append(w)

        if url is None:
            item.availability_note = "нет src" if not raw else "некорректный URL изображения"

        for cand in _parse_srcset(rec["srcset"]):
            cand_url = resolve(cand)
            if cand_url and _is_tracking_pixel(cand_url):
                continue
            add_candidate(cand, cand_url, "srcset")

    for srcset in parser.picture_source_srcsets:
        for cand in _parse_srcset(srcset):
            cand_url = resolve(cand)
            if cand_url and _is_tracking_pixel(cand_url):
                continue
            add_candidate(cand, cand_url, "srcset")

    if len(order) > IMAGES_MAX_PER_PAGE:
        order = order[:IMAGES_MAX_PER_PAGE]
        data.truncated = True
    elif data.total > IMAGES_MAX_PER_PAGE:
        data.truncated = True

    checkable = [it for it in order if it.src]
    if ctx is not None and checkable:
        await _check_availability(checkable, ctx)

    errors: list[str] = []
    warnings: list[str] = []
    checked = 0
    for it in order:
        if it.checked:
            checked += 1
        label = it.src or it.raw_src or "изображение"
        for e in it.errors:
            errors.append(f"{label}: {e}")
        for w in it.warnings:
            warnings.append(f"{label}: {w}")

    data.items = order
    data.checked_count = checked
    data.errors = errors
    data.warnings = warnings
    return data

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from .config import MAX_ROBOTS_BYTES
from .fetcher import safe_fetch


def robots_url_for(site_url: str) -> str:
    parts = urlsplit(site_url)
    return urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


async def fetch_robots(site_url: str) -> tuple[str, bool, str]:
    robots_url = robots_url_for(site_url)
    try:
        result = await safe_fetch(
            robots_url,
            max_bytes=MAX_ROBOTS_BYTES,
            accepted_content_types=("text/", "application/octet-stream"),
        )
    except Exception:
        return robots_url, False, ""

    if result.status_code != 200:
        return robots_url, False, ""

    return robots_url, True, result.content.decode("utf-8", errors="replace")


def sitemap_urls_from_robots(robots_text: str) -> list[str]:
    found: list[str] = []
    for raw_line in robots_text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() == "sitemap":
            candidate = value.strip()
            if candidate and candidate not in found:
                found.append(candidate)
    return found

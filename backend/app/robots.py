from urllib.parse import urlsplit, urlunsplit
from .config import MAX_ROBOTS_BYTES
from .fetcher import safe_fetch

def robots_url_for(site_url: str) -> str:
    p = urlsplit(site_url)
    return urlunsplit((p.scheme, p.netloc, "/robots.txt", "", ""))

async def fetch_robots(site_url: str):
    url = robots_url_for(site_url)
    try:
        r = await safe_fetch(url, max_bytes=MAX_ROBOTS_BYTES, accepted_content_types=("text/", "application/octet-stream"))
    except Exception:
        return url, False, ""
    if r.status_code != 200:
        return url, False, ""
    return url, True, r.content.decode("utf-8", errors="replace")

def sitemap_urls_from_robots(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        k,v = line.split(":",1)
        if k.strip().lower() == "sitemap":
            value = v.strip()
            if value and value not in out:
                out.append(value)
    return out

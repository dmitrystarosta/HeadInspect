from collections import deque
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET
from fastapi import HTTPException
from .config import MAX_AUDIT_URLS, MAX_SITEMAP_BYTES, MAX_SITEMAP_DEPTH, MAX_SITEMAPS
from .fetcher import safe_fetch
from .security import validate_public_url

def _local(tag): return tag.rsplit("}",1)[-1].lower()

def _parse(content: bytes):
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise HTTPException(status_code=502, detail="Invalid sitemap XML") from exc
    locs=[n.text.strip() for n in root.iter() if _local(n.tag)=="loc" and n.text and n.text.strip()]
    name=_local(root.tag)
    if name=="sitemapindex": return "index", locs
    if name=="urlset": return "urlset", locs
    raise HTTPException(status_code=502, detail="Unsupported sitemap XML")

async def discover_urls(site_url: str, initial: list[str]):
    if not initial:
        initial=[urljoin(site_url,"/sitemap.xml"), urljoin(site_url,"/sitemap_index.xml")]
    q=deque((u,0) for u in initial)
    seen_s, processed, pages, seen_p=set(), [], [], set()
    site_host=urlsplit(site_url).hostname
    while q and len(seen_s)<MAX_SITEMAPS and len(pages)<MAX_AUDIT_URLS:
        sm, depth=q.popleft()
        if depth>MAX_SITEMAP_DEPTH: continue
        try: sm=await validate_public_url(sm)
        except HTTPException: continue
        if sm in seen_s: continue
        seen_s.add(sm)
        try:
            r=await safe_fetch(sm,max_bytes=MAX_SITEMAP_BYTES,accepted_content_types=("xml","text/plain","application/octet-stream"))
        except HTTPException: continue
        if r.status_code!=200: continue
        try: kind,locs=_parse(r.content)
        except HTTPException: continue
        processed.append(r.url)
        if kind=="index":
            for child in locs:
                if len(seen_s)+len(q)>=MAX_SITEMAPS: break
                q.append((child,depth+1))
            continue
        for u in locs:
            if len(pages)>=MAX_AUDIT_URLS: break
            try: u=await validate_public_url(u)
            except HTTPException: continue
            if urlsplit(u).hostname!=site_host: continue
            if u not in seen_p:
                seen_p.add(u); pages.append(u)
    return pages, processed

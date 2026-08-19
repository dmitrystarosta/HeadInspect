import asyncio
from fastapi import HTTPException
from .analyzers.open_graph import analyze_open_graph
from .config import MAX_AUDIT_URLS, MAX_HTML_BYTES
from .fetcher import safe_fetch
from .htmlmeta import MetadataParser
from .models import AuditResponse, PageResult, OpenGraphData
from .robots import fetch_robots, sitemap_urls_from_robots
from .security import validate_public_url
from .sitemap import discover_urls

async def _analyze_page(url, sem):
    async with sem:
        try:
            r=await safe_fetch(url,max_bytes=MAX_HTML_BYTES,accepted_content_types=("text/html","application/xhtml+xml"))
        except HTTPException as exc:
            return PageResult(url=url,status_code=None,open_graph=OpenGraphData(),errors=[f"Страница недоступна: {exc.detail}"],warnings=[])
        errors=[]; warnings=[]
        if r.status_code>=400: errors.append(f"HTTP {r.status_code}")
        elif r.status_code>=300: warnings.append(f"HTTP {r.status_code}")
        p=MetadataParser()
        try: p.feed(r.content.decode("utf-8",errors="replace"))
        except Exception: errors.append("Не удалось разобрать HTML")
        og,oe,ow=analyze_open_graph(p.og); errors+=oe; warnings+=ow
        return PageResult(url=r.url,status_code=r.status_code,title=p.title,meta_description=p.meta_description,open_graph=og,errors=errors,warnings=warnings)

async def run_audit(raw_url):
    normalized=await validate_public_url(raw_url)
    robots_url,robots_found,robots_text=await fetch_robots(normalized)
    initial=sitemap_urls_from_robots(robots_text) if robots_found else []
    urls,sitemaps=await discover_urls(normalized,initial)
    if not urls: urls=[normalized]
    limited=len(urls)>=MAX_AUDIT_URLS
    sem=asyncio.Semaphore(4)
    results=await asyncio.gather(*(_analyze_page(u,sem) for u in urls))
    return AuditResponse(requested_url=raw_url,normalized_url=normalized,robots_url=robots_url,robots_found=robots_found,
        sitemap_urls=sitemaps,discovered_urls=len(urls),checked_urls=len(results),limited=limited,results=results)

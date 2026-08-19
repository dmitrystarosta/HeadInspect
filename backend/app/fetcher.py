from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import urljoin
import httpx
from fastapi import HTTPException
from .config import CONNECT_TIMEOUT, READ_TIMEOUT, MAX_REDIRECTS, USER_AGENT
from .security import normalize_public_url, resolve_and_validate_host

@dataclass
class FetchResult:
    url: str
    status_code: int
    headers: httpx.Headers
    content: bytes

def _timeout():
    return httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=READ_TIMEOUT, pool=CONNECT_TIMEOUT)

async def safe_fetch(url: str, *, max_bytes: int, accepted_content_types: tuple[str, ...] | None = None) -> FetchResult:
    current = normalize_public_url(url)
    async with httpx.AsyncClient(timeout=_timeout(), follow_redirects=False, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}) as client:
        for redirect_no in range(MAX_REDIRECTS + 1):
            await resolve_and_validate_host(current)
            try:
                async with client.stream("GET", current) as response:
                    if response.status_code in {301,302,303,307,308}:
                        location = response.headers.get("location")
                        if not location:
                            raise HTTPException(status_code=502, detail="Redirect without Location header")
                        if redirect_no >= MAX_REDIRECTS:
                            raise HTTPException(status_code=502, detail="Too many redirects")
                        current = normalize_public_url(urljoin(current, location))
                        continue
                    ctype = response.headers.get("content-type","").lower()
                    if accepted_content_types and ctype and not any(t in ctype for t in accepted_content_types):
                        raise HTTPException(status_code=502, detail=f"Unexpected content type: {ctype}")
                    chunks, total = [], 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise HTTPException(status_code=502, detail="Remote response is too large")
                        chunks.append(chunk)
                    return FetchResult(str(response.url), response.status_code, response.headers, b"".join(chunks))
            except httpx.TimeoutException as exc:
                raise HTTPException(status_code=504, detail=f"Timeout while fetching {current}") from exc
            except httpx.RequestError as exc:
                raise HTTPException(status_code=502, detail=f"Cannot fetch {current}") from exc
    raise HTTPException(status_code=502, detail="Fetch failed")

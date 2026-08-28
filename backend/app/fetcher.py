from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from fastapi import HTTPException

from .config import CONNECT_TIMEOUT, GLOBAL_MAX_CONCURRENT_FETCHES, MAX_REDIRECTS, READ_TIMEOUT, USER_AGENT
from .security import normalize_public_url, resolve_and_validate_host


@dataclass
class FetchResult:
    url: str
    status_code: int
    headers: httpx.Headers
    content: bytes


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=CONNECT_TIMEOUT,
        read=READ_TIMEOUT,
        write=READ_TIMEOUT,
        pool=CONNECT_TIMEOUT,
    )


# A single process-wide cap on the number of HTTP requests to third-party
# sites that may be in flight at the same time, regardless of how many audit
# jobs are currently running or what PAGE_CONCURRENCY is set to for a single
# job. Today MAX_CONCURRENT_AUDITS is 1, so this has no observable effect
# (PAGE_CONCURRENCY already caps a single job at the same number). It exists
# so that raising MAX_CONCURRENT_AUDITS in the future cannot silently turn
# the service into a source of unbounded concurrent traffic against audited
# sites: every outbound request, from every job, funnels through here.
_global_fetch_semaphore = asyncio.Semaphore(GLOBAL_MAX_CONCURRENT_FETCHES)


def _format_host_for_netloc(ip_literal: str) -> str:
    """Format a validated IP literal for use as a URL host, bracketing IPv6."""
    parsed = ipaddress.ip_address(ip_literal)
    if parsed.version == 6:
        return f"[{ip_literal}]"
    return ip_literal


async def safe_fetch(
    url: str,
    *,
    max_bytes: int,
    accepted_content_types: tuple[str, ...] | None = None,
    request_headers: dict[str, str] | None = None,
) -> FetchResult:
    current = normalize_public_url(url)

    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if request_headers:
        headers.update(request_headers)

    async with httpx.AsyncClient(
        timeout=_timeout(),
        follow_redirects=False,
        headers=headers,
    ) as client:
        for redirect_no in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            original_host = parts.hostname
            if not original_host:
                raise HTTPException(status_code=400, detail="Missing hostname")

            # Resolve and validate the host, then pin the *exact* address we
            # just validated for the real TCP connection below. If we instead
            # let httpx/httpcore re-resolve the hostname a second time when
            # opening the socket, a hostile authoritative DNS server with a
            # short TTL could return a public IP for this check and a
            # private/loopback/link-local/metadata address for the actual
            # connection a moment later (DNS rebinding / TOCTOU). Connecting
            # directly to the validated IP closes that gap: there is no
            # second, unpinned DNS lookup for httpx to abuse.
            pinned_ips = await resolve_and_validate_host(current)
            pinned_ip = pinned_ips[0]

            port = parts.port or (443 if parts.scheme == "https" else 80)
            pinned_netloc = _format_host_for_netloc(pinned_ip)
            if parts.port is not None:
                pinned_netloc = f"{pinned_netloc}:{port}"
            pinned_url = urlunsplit((parts.scheme, pinned_netloc, parts.path or "/", parts.query, ""))

            # The Host header keeps name-based virtual hosting working even
            # though we connect by IP. The "sni_hostname" extension keeps TLS
            # SNI (and certificate hostname verification) pinned to the real
            # hostname instead of the IP literal, for HTTPS targets.
            per_request_headers = {"Host": original_host}
            extensions: dict[str, object] = {}
            if parts.scheme == "https":
                extensions["sni_hostname"] = original_host

            try:
                request = client.build_request(
                    "GET",
                    pinned_url,
                    headers=per_request_headers,
                    extensions=extensions,
                )
            except httpx.InvalidURL as exc:
                raise HTTPException(status_code=400, detail="Invalid URL") from exc

            try:
                # Held for the full request lifecycle (connect through the
                # response body being fully read/closed), not just until
                # headers arrive - a slow body still occupies a connection
                # against the audited site and must count against the cap.
                async with _global_fetch_semaphore:
                    response = await client.send(request, stream=True)
                    try:
                        status = response.status_code

                        if status in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise HTTPException(status_code=502, detail="Redirect without Location header")
                            if redirect_no >= MAX_REDIRECTS:
                                raise HTTPException(status_code=502, detail="Too many redirects")
                            # Resolve the redirect relative to the *logical* URL
                            # (real hostname), never to the pinned-IP URL we
                            # actually connected to - otherwise a relative
                            # Location header would get joined onto an IP literal.
                            current = normalize_public_url(urljoin(current, location))
                            continue

                        content_type = response.headers.get("content-type", "").lower()
                        if accepted_content_types and content_type:
                            if not any(token in content_type for token in accepted_content_types):
                                raise HTTPException(
                                    status_code=502,
                                    detail=f"Unexpected content type: {content_type}",
                                )

                        content_length = response.headers.get("content-length")
                        if content_length:
                            try:
                                if int(content_length) > max_bytes:
                                    raise HTTPException(status_code=502, detail="Remote response is too large")
                            except ValueError:
                                pass

                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                raise HTTPException(status_code=502, detail="Remote response is too large")
                            chunks.append(chunk)

                        # Report the logical (hostname-based) URL, never the
                        # pinned-IP URL we actually connected to - callers
                        # (dedup, sitemap display, Meta/OG/Schema, etc.) must
                        # keep seeing the real hostname.
                        return FetchResult(
                            url=current,
                            status_code=status,
                            headers=response.headers,
                            content=b"".join(chunks),
                        )
                    finally:
                        await response.aclose()
            except httpx.TimeoutException as exc:
                raise HTTPException(status_code=504, detail=f"Timeout while fetching {current}") from exc
            except httpx.RequestError as exc:
                raise HTTPException(status_code=502, detail=f"Cannot fetch {current}") from exc

    raise HTTPException(status_code=502, detail="Fetch failed")

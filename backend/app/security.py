from __future__ import annotations
import asyncio, ipaddress, socket
from urllib.parse import urlsplit, urlunsplit
from fastapi import HTTPException

BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "metadata.google.internal"}
BLOCKED_IPS = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("100.100.100.200")}

def normalize_public_url(raw_url: str) -> str:
    raw_url = raw_url.strip()
    if not raw_url:
        raise HTTPException(status_code=400, detail="URL is required")
    if "://" not in raw_url:
        raw_url = "https://" + raw_url
    try:
        parts = urlsplit(raw_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid URL") from exc
    if parts.scheme.lower() not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Only HTTP and HTTPS are allowed")
    if not parts.hostname:
        raise HTTPException(status_code=400, detail="URL must contain a hostname")
    if parts.username is not None or parts.password is not None:
        raise HTTPException(status_code=400, detail="Credentials in URL are not allowed")
    hostname = parts.hostname.rstrip(".").lower()
    if hostname in BLOCKED_HOSTNAMES or hostname.endswith(".localhost"):
        raise HTTPException(status_code=400, detail="Local addresses are not allowed")
    port = parts.port
    if port is not None and port not in {80, 443}:
        raise HTTPException(status_code=400, detail="Only ports 80 and 443 are allowed")
    netloc = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))

def _is_forbidden_ip(ip):
    return (ip in BLOCKED_IPS or ip.is_loopback or ip.is_private or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified)

async def resolve_and_validate_host(url: str) -> list[str]:
    parts = urlsplit(url)
    hostname = parts.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="Missing hostname")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_forbidden_ip(literal):
            raise HTTPException(status_code=400, detail="Private or local IP addresses are not allowed")
        return [str(literal)]
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="Hostname cannot be resolved") from exc
    addresses = set()
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_forbidden_ip(ip):
            raise HTTPException(status_code=400, detail="Hostname resolves to a forbidden IP address")
        addresses.add(str(ip))
    if not addresses:
        raise HTTPException(status_code=400, detail="Hostname has no usable public IP address")
    return sorted(addresses)

async def validate_public_url(url: str) -> str:
    normalized = normalize_public_url(url)
    await resolve_and_validate_host(normalized)
    return normalized

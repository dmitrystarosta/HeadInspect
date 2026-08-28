"""Item 1 (DNS rebinding / TOCTOU) - the property-based-style IP matrix and
the rebinding regression test are the plan's automated-test checklist
items 7 and 10.
"""
from __future__ import annotations

import asyncio
import ipaddress

import pytest
from fastapi import HTTPException

from app import security


def test_normalize_public_url_adds_https_scheme():
    assert security.normalize_public_url("example.ru") == "https://example.ru/"


def test_normalize_public_url_lowercases_host():
    assert security.normalize_public_url("https://EXAMPLE.ru") == "https://example.ru/"


def test_normalize_public_url_strips_trailing_dot():
    assert security.normalize_public_url("https://example.ru./") == "https://example.ru/"


def test_normalize_public_url_rejects_non_http_scheme():
    with pytest.raises(HTTPException) as exc_info:
        security.normalize_public_url("ftp://example.ru")
    assert exc_info.value.status_code == 400


def test_normalize_public_url_rejects_credentials():
    with pytest.raises(HTTPException) as exc_info:
        security.normalize_public_url("https://user:pass@example.ru")
    assert exc_info.value.status_code == 400


def test_normalize_public_url_rejects_nonstandard_ports():
    with pytest.raises(HTTPException) as exc_info:
        security.normalize_public_url("https://example.ru:8080")
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    "literal,expected",
    [
        ("127.0.0.1", True),
        ("10.0.0.5", True),
        ("172.16.0.5", True),
        ("192.168.1.1", True),
        ("169.254.169.254", True),  # cloud metadata / link-local
        ("100.100.100.200", True),  # Alibaba Cloud metadata (explicit block)
        ("224.0.0.1", True),  # multicast
        ("0.0.0.0", True),  # unspecified
        ("::1", True),  # IPv6 loopback
        ("fe80::1", True),  # IPv6 link-local
        ("::ffff:127.0.0.1", True),  # IPv4-mapped IPv6 loopback
        ("8.8.8.8", False),
        ("1.1.1.1", False),
        ("93.184.216.34", False),
    ],
)
def test_is_forbidden_ip_matrix(literal, expected):
    ip = ipaddress.ip_address(literal)
    assert security._is_forbidden_ip(ip) is expected


async def test_resolve_and_validate_host_blocks_dns_rebinding(monkeypatch):
    """Regression test for P0-1: two independent resolutions of the same
    URL must each be validated on their own merits. The first returns a
    public IP (passes); the second simulates a hostile DNS server flipping
    to a cloud-metadata address on the very next lookup (must be blocked).
    This proves resolve_and_validate_host itself never "trusts" a
    previous result - see test_fetcher_pinning.py for proof that fetcher.py
    then *uses* this validated address for the real connection instead of
    letting httpx resolve a second time.
    """
    call_count = {"n": 0}

    async def fake_getaddrinfo(host, port, type=None):
        call_count["n"] += 1
        ip = "93.184.216.34" if call_count["n"] == 1 else "169.254.169.254"
        return [(None, None, None, None, (ip, 443))]

    class FakeLoop:
        def getaddrinfo(self, host, port, type=None):
            return fake_getaddrinfo(host, port, type=type)

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())

    first = await security.resolve_and_validate_host("https://evil.example/")
    assert first == ["93.184.216.34"]

    with pytest.raises(HTTPException) as exc_info:
        await security.resolve_and_validate_host("https://evil.example/")
    assert exc_info.value.status_code == 400

"""Integration: the real analyze_page must attach CanonicalData built from the
fetched HTML and response headers, without disturbing the existing Open
Graph / Meta / Schema analysis on the same page.
"""
from __future__ import annotations

import asyncio

from app import audit as audit_module


class FakeFetchResult:
    def __init__(self, url, status_code=200, headers=None, content=b""):
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content


PAGE_HTML = (
    b"<html lang='ru'><head>"
    b"<title>A perfectly reasonable title for the page here</title>"
    b"<meta name='description' content='"
    + b"x" * 130 +
    b"'>"
    b"<meta property='og:title' content='OG title'>"
    b"<meta property='og:image' content='https://ex.ru/og.png'>"
    b"<link rel='canonical' href='https://ex.ru/page'>"
    b"</head><body></body></html>"
)


async def _run(url="https://ex.ru/page", headers=None):
    async def fake_safe_fetch(u, **kwargs):
        return FakeFetchResult(
            url=url,
            status_code=200,
            headers=headers or {"content-type": "text/html; charset=utf-8"},
            content=PAGE_HTML,
        )

    audit_module.safe_fetch  # sanity: attribute exists
    import pytest  # noqa: F401 (keep import parity with sibling tests)

    from unittest.mock import patch

    with patch.object(audit_module, "safe_fetch", fake_safe_fetch):
        return await audit_module.analyze_page(url, asyncio.Semaphore(1))


def test_analyze_page_attaches_self_canonical():
    result = asyncio.run(_run())
    assert result.check_failed is False
    assert result.canonical.present is True
    assert result.canonical.is_self is True
    assert result.canonical.errors == []
    # Existing modules still populated on the same page:
    assert result.title.startswith("A perfectly reasonable")
    assert result.open_graph is not None
    assert result.meta is not None
    assert result.schema_data is not None


def test_analyze_page_canonical_uses_x_robots_header():
    result = asyncio.run(_run(headers={
        "content-type": "text/html; charset=utf-8",
        "x-robots-tag": "noindex",
    }))
    assert result.canonical.page_noindex is True


def test_analyze_page_canonical_reads_http_link_header():
    result = asyncio.run(_run(headers={
        "content-type": "text/html; charset=utf-8",
        "link": '<https://ex.ru/page>; rel="canonical"',
    }))
    # HTML says /page and header says /page -> agree -> duplication warning,
    # both sources recorded, still self-referencing.
    assert result.canonical.source == "both"
    assert result.canonical.is_self is True

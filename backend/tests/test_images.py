"""Images module: markup analysis + HEAD-only availability.

The analyzer never downloads image bytes, so these tests fake safe_fetch to
return HEAD-style results (status + headers, empty body) and assert both the
markup findings (alt, width/height, loading, srcset/<picture>, format guess)
and the availability handling (accessible / broken / inconclusive), plus the
per-audit dedup, caps and that an image failure never raises.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.analyzers import images as images_module
from app.analyzers.images import ImageAuditContext, analyze_images
from app.htmlmeta import MetadataParser
from app.jobs import JobManager
from app.models import ImageItem, ImagesData, PageResult


class FakeResp:
    def __init__(self, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.content = b""


def install_fake_head(monkeypatch, registry, *, calls=None, boom=()):
    """registry: url -> (status, headers dict). boom: urls that raise (transport
    error). calls: optional list to record each fetched url (dedup assertions)."""
    async def fake_safe_fetch(url, *, max_bytes, method="GET", request_headers=None,
                              accepted_content_types=None):
        if calls is not None:
            calls.append((url, method))
        if url in boom:
            raise HTTPException(status_code=502, detail="Cannot fetch")
        status, headers = registry.get(url, (200, {"content-type": "image/png", "content-length": "1000"}))
        return FakeResp(status, headers)

    monkeypatch.setattr(images_module, "safe_fetch", fake_safe_fetch)


def parse(html: str) -> MetadataParser:
    p = MetadataParser()
    p.feed(html)
    return p


async def test_markup_alt_dimensions_loading(monkeypatch):
    install_fake_head(monkeypatch, {})
    html = """
    <img src="/a.png" width="100" height="50">            <!-- no alt -->
    <img src="/b.png" alt="" width="10" height="10">      <!-- decorative, ok -->
    <img src="/c.png" alt="Нормальный alt">               <!-- no width/height -->
    """
    data = await analyze_images(parse(html), "https://site.ru/", ImageAuditContext())
    by_src = {it.src: it for it in data.items}
    a = by_src["https://site.ru/a.png"]
    b = by_src["https://site.ru/b.png"]
    c = by_src["https://site.ru/c.png"]
    assert "нет атрибута alt" in a.warnings
    assert b.alt_empty is True and not any("alt" in w for w in b.warnings)
    assert any("width/height" in w for w in c.warnings)
    assert data.total == 3


async def test_first_image_lazy_warns(monkeypatch):
    install_fake_head(monkeypatch, {})
    html = '<img src="/hero.png" alt="Hero" width="1" height="1" loading="lazy">'
    data = await analyze_images(parse(html), "https://site.ru/", ImageAuditContext())
    assert any("lazy" in w for w in data.items[0].warnings)


async def test_format_guessed_from_extension(monkeypatch):
    install_fake_head(monkeypatch, {})
    html = '<img src="/x.webp" alt="a" width="1" height="1"><img src="/nodot" alt="b" width="1" height="1">'
    data = await analyze_images(parse(html), "https://site.ru/", ImageAuditContext())
    by_src = {it.src: it for it in data.items}
    assert by_src["https://site.ru/x.webp"].format_guess == "WEBP"
    assert by_src["https://site.ru/nodot"].format_guess is None


async def test_availability_statuses(monkeypatch):
    registry = {
        "https://site.ru/ok.png": (200, {"content-type": "image/png", "content-length": "2048"}),
        "https://site.ru/gone.png": (404, {}),
        "https://site.ru/nohead.png": (405, {}),
        "https://site.ru/html.png": (200, {"content-type": "text/html"}),
        "https://site.ru/heavy.png": (200, {"content-type": "image/jpeg",
                                            "content-length": str(5 * 1024 * 1024)}),
        "https://site.ru/nosize.png": (200, {"content-type": "image/png"}),
    }
    install_fake_head(monkeypatch, registry)
    html = "".join(
        f'<img src="/{n}.png" alt="x" width="1" height="1">'
        for n in ["ok", "gone", "nohead", "html", "heavy", "nosize"]
    )
    data = await analyze_images(parse(html), "https://site.ru/", ImageAuditContext())
    by_src = {it.src: it for it in data.items}

    assert by_src["https://site.ru/ok.png"].accessible is True
    assert by_src["https://site.ru/ok.png"].bytes == 2048

    gone = by_src["https://site.ru/gone.png"]
    assert gone.accessible is False and "HTTP 404" in gone.errors

    nohead = by_src["https://site.ru/nohead.png"]
    assert nohead.accessible is None and "HEAD" in (nohead.availability_note or "")

    assert any("Content-Type" in w for w in by_src["https://site.ru/html.png"].warnings)
    assert any("тяжёл" in w for w in by_src["https://site.ru/heavy.png"].warnings)
    assert "размер не сообщён" in (by_src["https://site.ru/nosize.png"].availability_note or "")


async def test_transport_error_is_inconclusive_not_broken(monkeypatch):
    install_fake_head(monkeypatch, {}, boom={"https://site.ru/x.png"})
    html = '<img src="/x.png" alt="a" width="1" height="1">'
    data = await analyze_images(parse(html), "https://site.ru/", ImageAuditContext())
    item = data.items[0]
    assert item.accessible is None
    assert "не удалось определить" in (item.availability_note or "")
    assert item.errors == []


async def test_head_is_deduplicated_per_audit(monkeypatch):
    calls = []
    install_fake_head(monkeypatch, {}, calls=calls)
    ctx = ImageAuditContext()
    # Same URL on two pages -> fetched once.
    html = '<img src="/logo.png" alt="logo" width="1" height="1">'
    await analyze_images(parse(html), "https://site.ru/a", ctx)
    await analyze_images(parse(html), "https://site.ru/b", ctx)
    assert [u for u, m in calls] == ["https://site.ru/logo.png"]
    assert all(m == "HEAD" for _, m in calls)


async def test_per_page_cap_marks_truncated(monkeypatch):
    install_fake_head(monkeypatch, {})
    monkeypatch.setattr(images_module, "IMAGES_MAX_PER_PAGE", 2)
    html = "".join(f'<img src="/i{i}.png" alt="x" width="1" height="1">' for i in range(5))
    data = await analyze_images(parse(html), "https://site.ru/", ImageAuditContext())
    assert data.total == 5
    assert data.truncated is True
    assert len(data.items) == 2


async def test_unique_fetch_cap_per_audit(monkeypatch):
    calls = []
    install_fake_head(monkeypatch, {}, calls=calls)
    monkeypatch.setattr(images_module, "IMAGES_MAX_UNIQUE_FETCH_PER_AUDIT", 1)
    ctx = ImageAuditContext()
    html = '<img src="/a.png" alt="a" width="1" height="1"><img src="/b.png" alt="b" width="1" height="1">'
    data = await analyze_images(parse(html), "https://site.ru/", ctx)
    assert len(calls) == 1  # second unique URL not fetched
    skipped = [it for it in data.items if it.availability_note and "лимит проверок" in it.availability_note]
    assert skipped


async def test_srcset_and_picture_candidates_collected(monkeypatch):
    install_fake_head(monkeypatch, {})
    html = """
    <img src="/main.png" srcset="/main-2x.png 2x" alt="a" width="1" height="1">
    <picture><source srcset="/alt.webp 1x, /alt-2x.webp 2x"><img src="/fallback.png" alt="b" width="1" height="1"></picture>
    """
    data = await analyze_images(parse(html), "https://site.ru/", ImageAuditContext())
    srcs = {it.src for it in data.items}
    assert "https://site.ru/main-2x.png" in srcs
    assert "https://site.ru/alt.webp" in srcs and "https://site.ru/alt-2x.webp" in srcs
    srcset_items = [it for it in data.items if it.source == "srcset"]
    assert srcset_items and all(not it.has_alt for it in srcset_items)


async def test_ctx_none_does_markup_only_no_network(monkeypatch):
    # With no audit context, no HEAD is attempted at all.
    async def boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("safe_fetch must not be called when ctx is None")
    monkeypatch.setattr(images_module, "safe_fetch", boom)
    html = '<img src="/a.png" width="1" height="1">'
    data = await analyze_images(parse(html), "https://site.ru/", None)
    assert data.checked_count == 0
    assert data.items[0].checked is False
    assert "нет атрибута alt" in data.items[0].warnings


async def test_aggregate_errors_and_warnings(monkeypatch):
    install_fake_head(monkeypatch, {"https://site.ru/gone.png": (404, {})})
    html = '<img src="/gone.png"><img src="/ok.png" alt="x" width="1" height="1">'
    data = await analyze_images(parse(html), "https://site.ru/", ImageAuditContext())
    assert any("HTTP 404" in e for e in data.errors)
    assert any("нет атрибута alt" in w for w in data.warnings)


def test_image_reuse_counts_across_pages():
    # _apply_image_reuse (jobs) sets reuse_count = number of pages using a URL.
    shared = ImageItem(src="https://site.ru/logo.png", source="img")
    page1 = PageResult(url="https://site.ru/a", images=ImagesData(items=[shared]))
    page2 = PageResult(url="https://site.ru/b",
                       images=ImagesData(items=[ImageItem(src="https://site.ru/logo.png", source="img")]))
    page3 = PageResult(url="https://site.ru/c",
                       images=ImagesData(items=[ImageItem(src="https://site.ru/solo.png", source="img")]))
    JobManager._apply_image_reuse([page1, page2, page3])
    assert page1.images.items[0].reuse_count == 2
    assert page3.images.items[0].reuse_count == 1

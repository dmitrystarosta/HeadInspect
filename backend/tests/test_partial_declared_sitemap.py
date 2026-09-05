"""completed_partial for the "declared sitemap could not be fetched" case.

The status must become completed_partial ONLY when:
  * a sitemap was explicitly declared in robots.txt,
  * it could not be fetched/parsed,
  * discovery therefore fell back to the entry page only,
  * the entry page itself audited fine.

It must NOT become completed_partial for:
  * a site with no sitemap at all,
  * a guessed /sitemap.xml or /sitemap_index.xml that simply does not exist,
  * a sitemap fetched successfully that genuinely lists a single page,
  * a normal, fully-discovered site.

The first block tests the discover_audit_urls signal that drives the decision;
the second drives JobManager._run end-to-end and asserts the final status.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fastapi import HTTPException

from app import audit, sitemap
from app import jobs as jobs_module
from app.jobs import Job, JobManager
from app.models import PageResult


@dataclass
class FakeFetchResult:
    url: str
    status_code: int = 200
    headers: dict | None = None
    content: bytes = b""

    def __post_init__(self):
        if self.headers is None:
            self.headers = {}


def _urlset(*paths):
    locs = "".join(f"<url><loc>https://example.ru{p}</loc></url>" for p in paths)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'.encode()


async def _identity(url):
    return url


def _wire_common(monkeypatch, *, robots_text, robots_found=True):
    async def entry_fetch(url, **kwargs):
        return FakeFetchResult(url=url, status_code=200, headers={"content-type": "text/html"}, content=b"<html></html>")

    async def fake_robots(site_url):
        return ("https://example.ru/robots.txt", robots_found, robots_text)

    monkeypatch.setattr(audit, "validate_public_url", _identity)
    monkeypatch.setattr(audit, "safe_fetch", entry_fetch)
    monkeypatch.setattr(audit, "fetch_robots", fake_robots)
    monkeypatch.setattr(sitemap, "validate_public_url", _identity)


# --- discover_audit_urls signal -------------------------------------------

async def test_signal_true_when_declared_sitemap_unfetchable(monkeypatch):
    _wire_common(monkeypatch, robots_text="Sitemap: https://example.ru/sitemap.xml\n")

    async def sitemap_fetch(url, **kwargs):
        raise HTTPException(status_code=504, detail="Timeout")

    monkeypatch.setattr(sitemap, "safe_fetch", sitemap_fetch)

    d = await audit.discover_audit_urls("https://example.ru/")
    assert d["sitemap_declared_unfetched"] is True
    assert d["urls"] == ["https://example.ru/"]          # fell back to entry only
    assert d["sitemap_issues"]                            # reason recorded


async def test_signal_false_when_no_sitemap_declared_and_guess_misses(monkeypatch):
    # robots.txt exists but declares no sitemap -> guessed defaults are tried
    # and miss. A small site with no sitemap must NOT be flagged partial.
    _wire_common(monkeypatch, robots_text="User-agent: *\nAllow: /\n")

    async def sitemap_fetch(url, **kwargs):
        raise HTTPException(status_code=404, detail="Not found")

    monkeypatch.setattr(sitemap, "safe_fetch", sitemap_fetch)

    d = await audit.discover_audit_urls("https://example.ru/")
    assert d["sitemap_declared_unfetched"] is False
    assert d["sitemap_issues"] == []                     # guessed misses stay silent


async def test_signal_false_when_no_robots_at_all(monkeypatch):
    _wire_common(monkeypatch, robots_text="", robots_found=False)

    async def sitemap_fetch(url, **kwargs):
        raise HTTPException(status_code=404, detail="Not found")

    monkeypatch.setattr(sitemap, "safe_fetch", sitemap_fetch)

    d = await audit.discover_audit_urls("https://example.ru/")
    assert d["sitemap_declared_unfetched"] is False
    assert d["sitemap_issues"] == []


async def test_signal_false_when_declared_sitemap_has_single_page(monkeypatch):
    # Sitemap fetched fine and genuinely lists exactly one page -> normal
    # completion, not partial.
    _wire_common(monkeypatch, robots_text="Sitemap: https://example.ru/sitemap.xml\n")

    async def sitemap_fetch(url, **kwargs):
        return FakeFetchResult(url=url, content=_urlset("/"), headers={"content-type": "application/xml"})

    monkeypatch.setattr(sitemap, "safe_fetch", sitemap_fetch)

    d = await audit.discover_audit_urls("https://example.ru/")
    assert d["sitemap_declared_unfetched"] is False
    assert d["sitemap_issues"] == []
    assert d["urls"] == ["https://example.ru/"]          # one real page, from the sitemap


async def test_signal_false_when_discovery_is_complete(monkeypatch):
    _wire_common(monkeypatch, robots_text="Sitemap: https://example.ru/sitemap.xml\n")

    async def sitemap_fetch(url, **kwargs):
        return FakeFetchResult(url=url, content=_urlset("/", "/about", "/contact"), headers={"content-type": "application/xml"})

    monkeypatch.setattr(sitemap, "safe_fetch", sitemap_fetch)

    d = await audit.discover_audit_urls("https://example.ru/")
    assert d["sitemap_declared_unfetched"] is False
    assert len(d["urls"]) == 3


# --- JobManager end-to-end status -----------------------------------------

def _discovery(**overrides):
    base = {
        "normalized_url": "https://example.ru/",
        "robots_url": "https://example.ru/robots.txt",
        "robots_found": True,
        "robots_sitemap_urls": ["https://example.ru/sitemap.xml"],
        "sitemap_urls": [],
        "sitemap_issues": [],
        "urls": ["https://example.ru/"],
        "limited": False,
        "access_blocked_status": None,
        "sitemap_declared_unfetched": False,
    }
    base.update(overrides)
    return base


async def _run_job(monkeypatch, discovery):
    monkeypatch.setattr(jobs_module, "AUDIT_TIMEOUT", 30)

    async def discover(url):
        return discovery

    async def run_pages(urls, on_result, *, stop_event=None):
        for u in urls:
            await on_result(PageResult(url=u, requested_url=u, status_code=200))

    monkeypatch.setattr(jobs_module, "discover_audit_urls", discover)
    monkeypatch.setattr(jobs_module, "run_pages", run_pages)

    manager = JobManager()
    job = Job(job_id="j", requested_url="https://example.ru/")
    manager.jobs[job.job_id] = job
    await manager._run(job)
    return job


async def test_job_declared_sitemap_unfetched_is_completed_partial(monkeypatch):
    job = await _run_job(monkeypatch, _discovery(
        sitemap_declared_unfetched=True,
        sitemap_issues=["https://example.ru/sitemap.xml: не удалось получить sitemap (Timeout)"],
    ))
    assert job.status == "completed_partial"
    assert job.partial_reason is not None
    assert "robots.txt" in job.partial_reason
    assert "стартовая страница" in job.partial_reason.lower()
    assert job.blocked_mid_audit is False               # not the block reason
    assert len(job.results) == 1                         # entry page audited fine


async def test_job_normal_single_page_is_completed_not_partial(monkeypatch):
    job = await _run_job(monkeypatch, _discovery())      # signal False by default
    assert job.status == "completed"
    assert job.partial_reason is None


async def test_job_no_sitemap_small_site_is_completed_not_partial(monkeypatch):
    job = await _run_job(monkeypatch, _discovery(
        robots_found=True,
        robots_sitemap_urls=[],
        sitemap_urls=[],
        sitemap_issues=[],
        sitemap_declared_unfetched=False,
    ))
    assert job.status == "completed"
    assert job.partial_reason is None

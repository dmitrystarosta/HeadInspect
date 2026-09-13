"""With MAX_CONCURRENT_AUDITS=1, a second audit created while the first still
holds the single execution slot must be observable as "queued" - both on the
Job and through the exact model the /api/audits/{id} endpoint returns - and must
start automatically once the slot frees. This is the state the home page needs
to honestly show a "waiting in queue" notice instead of a frozen-looking audit.

No backend behaviour is changed here; this only pins the existing lifecycle the
frontend depends on.
"""
from __future__ import annotations

import asyncio

from app import jobs as jobs_module
from app.jobs import JobManager


async def test_second_audit_is_queued_while_slot_held_then_runs_when_freed(monkeypatch):
    gate = asyncio.Event()  # job A stays inside the slot until this opens

    async def gated_discover(url):
        # Job A blocks here *inside* the single slot until the gate opens; job B
        # never reaches discovery because it is still waiting for the slot.
        if "a.example" in url:
            await gate.wait()
        return {
            "normalized_url": url,
            "robots_url": None,
            "robots_found": None,
            "robots_sitemap_urls": [],
            "sitemap_urls": [],
            "sitemap_issues": [],
            "urls": [],
            "limited": False,
            "access_blocked_status": None,
        }

    async def fake_run_pages(urls, on_result, *, stop_event=None):
        return  # nothing to crawl -> completes immediately once past discovery

    monkeypatch.setattr(jobs_module, "discover_audit_urls", gated_discover)
    monkeypatch.setattr(jobs_module, "run_pages", fake_run_pages)

    manager = JobManager()

    job_a = await manager.create("https://a.example.ru/", client_ip="1.1.1.1")
    # Let A's background task start and grab the only slot.
    for _ in range(100):
        await asyncio.sleep(0.01)
        if job_a.status == "discovering":
            break
    assert job_a.status == "discovering"  # A holds the slot, blocked on the gate

    # Different host + IP so neither domain cooldown nor per-IP rate limit
    # interferes - we are testing the queue, nothing else.
    job_b = await manager.create("https://b.example.ru/", client_ip="2.2.2.2")
    await asyncio.sleep(0.05)

    # B is created but cannot start: honestly reported as queued, both on the
    # object and through the model the status endpoint serializes.
    assert job_b.status == "queued"
    assert manager.status_model(job_b).status == "queued"

    # Free the slot: A finishes, and B must then start and run to completion on
    # its own, with no further input.
    gate.set()
    for _ in range(300):
        await asyncio.sleep(0.01)
        if job_b.status in ("completed", "completed_partial"):
            break

    assert job_a.status == "completed"
    assert job_b.status == "completed"

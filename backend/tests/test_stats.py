"""Privacy-preserving audit-run stats store (app/stats.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import stats


def _enable(monkeypatch, tmp_path):
    monkeypatch.setenv("HEADINSPECT_STATS_DB", str(tmp_path / "stats.db"))


def test_disabled_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv("HEADINSPECT_STATS_DB", raising=False)
    assert stats.enabled() is False
    # All operations are safe no-ops and create nothing.
    stats.record_created("j", "a.ru", "meta", datetime.now(timezone.utc))
    assert stats.runs_between(datetime.now(timezone.utc), datetime.now(timezone.utc)) == []
    assert not (tmp_path / "stats.db").exists()


def test_normalize_source():
    assert stats.normalize_source("Meta") == "meta"
    assert stats.normalize_source(None) == "general"
    assert stats.normalize_source("totally-unknown") == "other"
    assert stats.normalize_source("images") == "images"


def test_record_status_and_window(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    base = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    stats.record_created("j1", "a.ru", "meta", base)
    stats.record_status("j1", "completed", base)
    # Outside the query window (two days earlier).
    stats.record_created("j2", "b.ru", "images", base - timedelta(days=2))

    runs = stats.runs_between(base - timedelta(hours=1), base + timedelta(hours=1))
    assert [r["site"] for r in runs] == ["a.ru"]
    assert runs[0]["status"] == "completed"
    assert runs[0]["source"] == "meta"


def test_record_created_is_idempotent(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    base = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    stats.record_created("j1", "a.ru", "meta", base)
    stats.record_created("j1", "a.ru", "meta", base)  # INSERT OR IGNORE
    runs = stats.runs_between(base - timedelta(hours=1), base + timedelta(hours=1))
    assert len(runs) == 1


def test_cleanup_removes_only_old(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    stats.record_created("old", "old.ru", "general", now - timedelta(days=40))
    stats.record_created("new", "new.ru", "general", now - timedelta(days=1))
    removed = stats.cleanup(now - timedelta(days=30))
    assert removed == 1
    runs = stats.runs_between(now - timedelta(days=60), now + timedelta(days=1))
    assert [r["site"] for r in runs] == ["new.ru"]


def test_reconcile_running_marks_interrupted(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    base = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    stats.record_created("r1", "a.ru", "general", base)
    stats.record_status("r1", "running", base)
    stats.record_created("q1", "b.ru", "meta", base)  # stays "queued"
    stats.record_created("c1", "c.ru", "images", base)
    stats.record_status("c1", "completed", base)

    assert stats.reconcile_running() == 2  # only the non-terminal rows

    by_site = {r["site"]: r["status"]
               for r in stats.runs_between(base - timedelta(hours=1), base + timedelta(hours=1))}
    assert by_site["a.ru"] == "interrupted"
    assert by_site["b.ru"] == "interrupted"
    assert by_site["c.ru"] == "completed"  # terminal status untouched


def test_sent_markers(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    assert stats.already_sent("2026-10-25") is False
    stats.mark_sent("2026-10-25", datetime.now(timezone.utc))
    assert stats.already_sent("2026-10-25") is True
    assert stats.already_sent("2026-10-26") is False

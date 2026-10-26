"""Minimal, privacy-preserving audit-run statistics for the daily email report.

Stores ONLY non-personal run metadata — site hostname, audit type (source),
status and timestamps — in a small SQLite file. No IP addresses, no user
emails, no User-Agent, no audit results. Entirely optional: when the
HEADINSPECT_STATS_DB environment variable is not set the whole module is a
no-op, so the service behaves exactly as before and no file is ever created.

Every write is wrapped so a stats failure can never break or slow an audit;
writes are small and infrequent (audits are rate-limited), so load is
negligible. Old rows are pruned by the daily report (see daily_report.py).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger("uvicorn.error")

# Known audit surfaces; anything else is normalised to "other" so a crafted
# `source` value can never bloat or pollute the table.
_ALLOWED_SOURCES = {
    "general", "open-graph", "meta", "canonical", "schema", "images", "sitemap",
}

_lock = threading.Lock()


def db_path() -> str | None:
    path = os.getenv("HEADINSPECT_STATS_DB")
    return path or None


def enabled() -> bool:
    return db_path() is not None


def normalize_source(source: str | None) -> str:
    if not source:
        return "general"
    value = str(source).strip().lower()[:32]
    return value if value in _ALLOWED_SOURCES else "other"


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


@contextmanager
def _connect():
    conn = sqlite3.connect(db_path(), timeout=5.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _ensure(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS audit_runs (
            job_id     TEXT PRIMARY KEY,
            site       TEXT,
            source     TEXT,
            status     TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_runs_created ON audit_runs(created_at)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS report_sent (
            report_date TEXT PRIMARY KEY,
            sent_at     TEXT NOT NULL
        )"""
    )


_NON_TERMINAL_STATUSES = ("queued", "discovering", "running")


def init_db() -> None:
    if not enabled():
        return
    try:
        with _lock, _connect():
            pass
    except Exception:
        logger.exception("stats.init_db failed")


def reconcile_running() -> int:
    """Mark leftover non-terminal runs as 'interrupted'. Jobs live only in the
    API process's memory, so at process startup nothing is actually running:
    any row still 'queued'/'discovering'/'running' is a leftover from a
    previous (crashed or restarted) process and must not stay 'running' forever.
    Called once at API startup. Returns how many rows were reconciled."""
    if not enabled():
        return 0
    try:
        with _lock, _connect() as conn:
            placeholders = ",".join("?" * len(_NON_TERMINAL_STATUSES))
            cur = conn.execute(
                f"UPDATE audit_runs SET status = 'interrupted' WHERE status IN ({placeholders})",
                _NON_TERMINAL_STATUSES,
            )
            return cur.rowcount
    except Exception:
        logger.exception("stats.reconcile_running failed")
        return 0


def record_created(job_id: str, site: str | None, source: str | None, created_at: datetime) -> None:
    if not enabled():
        return
    try:
        with _lock, _connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO audit_runs (job_id, site, source, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, site or "", normalize_source(source), "queued", _iso(created_at)),
            )
    except Exception:
        logger.exception("stats.record_created failed")


def record_status(job_id: str, status: str, started_at: datetime | None = None) -> None:
    if not enabled():
        return
    try:
        with _lock, _connect() as conn:
            conn.execute(
                "UPDATE audit_runs SET status = ?, started_at = COALESCE(?, started_at) WHERE job_id = ?",
                (status, _iso(started_at), job_id),
            )
    except Exception:
        logger.exception("stats.record_status failed")


def runs_between(start_utc: datetime, end_utc: datetime) -> list[dict]:
    """Runs whose created_at falls in [start_utc, end_utc). Includes runs that
    are still in progress (their current status is returned as-is)."""
    if not enabled():
        return []
    try:
        with _lock, _connect() as conn:
            cur = conn.execute(
                "SELECT site, source, status, created_at, started_at FROM audit_runs "
                "WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
                (_iso(start_utc), _iso(end_utc)),
            )
            cols = ("site", "source", "status", "created_at", "started_at")
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        logger.exception("stats.runs_between failed")
        return []


def cleanup(before_utc: datetime) -> int:
    if not enabled():
        return 0
    try:
        with _lock, _connect() as conn:
            cur = conn.execute("DELETE FROM audit_runs WHERE created_at < ?", (_iso(before_utc),))
            return cur.rowcount
    except Exception:
        logger.exception("stats.cleanup failed")
        return 0


def already_sent(report_date: str) -> bool:
    if not enabled():
        return False
    try:
        with _lock, _connect() as conn:
            return conn.execute(
                "SELECT 1 FROM report_sent WHERE report_date = ?", (report_date,)
            ).fetchone() is not None
    except Exception:
        logger.exception("stats.already_sent failed")
        return False


def mark_sent(report_date: str, sent_at: datetime) -> None:
    if not enabled():
        return
    # No try/except swallow here beyond logging: the caller marks only AFTER a
    # successful send, and a failure to mark would (at worst) allow a duplicate,
    # which the PRIMARY KEY still prevents on retry.
    try:
        with _lock, _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO report_sent (report_date, sent_at) VALUES (?, ?)",
                (report_date, _iso(sent_at)),
            )
    except Exception:
        logger.exception("stats.mark_sent failed")

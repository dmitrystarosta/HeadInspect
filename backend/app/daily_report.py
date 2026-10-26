"""Daily email report for the HeadInspect owner.

Run once a day (host cron → `docker compose exec -T api python -m app.daily_report`,
recommended at 00:05 MSK) to email a compact HTML+text summary of the PREVIOUS
Moscow calendar day: backend state, how many audits ran, unique sites,
completed / failed / unfinished counts, and a table of every run (domain, MSK
start time, audit type, status). Runs started on the report day are included
even if still in progress, with their current status.

Privacy: uses only the non-personal metadata in stats.py (site, type, status,
timestamps) — never IPs, emails, User-Agents or audit results. Duplicate sends
for the same date are prevented via stats.report_sent; the date is marked only
AFTER a successful send, so a failed send can simply be retried. No attachments.
All configuration (recipient, SMTP) comes from environment variables; no
secrets live in the repo.
"""
from __future__ import annotations

import fcntl
import logging
import os
import smtplib
import sys
import urllib.request
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape

from . import stats

logger = logging.getLogger("headinspect.daily_report")

# Moscow has had a fixed UTC+3 offset (no DST) since 2014, so a fixed offset is
# correct and avoids any dependency on the tzdata package inside the slim image.
MSK = timezone(timedelta(hours=3))

STATS_RETENTION_DAYS = 30
SMTP_TIMEOUT = 20.0

_STATUS_LABELS = {
    "queued": "в очереди",
    "discovering": "поиск страниц",
    "running": "выполняется",
    "completed": "завершена",
    "completed_partial": "завершена частично",
    "failed": "ошибка",
    "interrupted": "прервана (перезапуск)",
}
_SOURCE_LABELS = {
    "general": "Общий аудит",
    "open-graph": "Open Graph",
    "meta": "Meta",
    "canonical": "Canonical",
    "schema": "Schema",
    "images": "Images",
    "sitemap": "Sitemap",
    "other": "—",
}


def report_window(now_msk: datetime) -> tuple[str, datetime, datetime]:
    """Return (report_date_iso, start_utc, end_utc) for the previous Moscow
    calendar day relative to now_msk."""
    report_day: date = (now_msk - timedelta(days=1)).date()
    start_msk = datetime(report_day.year, report_day.month, report_day.day, tzinfo=MSK)
    end_msk = start_msk + timedelta(days=1)
    return report_day.isoformat(), start_msk.astimezone(timezone.utc), end_msk.astimezone(timezone.utc)


def check_backend() -> str:
    """Light liveness check of the API's own /health endpoint (inside the
    container), NOT external monitoring of the public site. Returns a short
    human label. Never raises."""
    url = os.getenv("HEADINSPECT_HEALTH_URL", "http://127.0.0.1:8000/health")
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (fixed internal URL)
            status = getattr(resp, "status", None) or resp.getcode()
            return "доступен" if 200 <= status < 300 else f"ответил HTTP {status}"
    except Exception:
        return "не отвечает"


def _msk_time(iso_utc: str | None) -> str:
    if not iso_utc:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(MSK).strftime("%H:%M")
    except ValueError:
        return "—"


def _summarize(runs: list[dict]) -> dict:
    total = len(runs)
    completed = sum(1 for r in runs if r["status"] == "completed")
    failed = sum(1 for r in runs if r["status"] == "failed")
    return {
        "total": total,
        "sites": len({r["site"] for r in runs if r["site"]}),
        "completed": completed,
        "failed": failed,
        # Everything that is neither cleanly completed nor failed: partial
        # results and runs still in progress at send time.
        "unfinished": total - completed - failed,
    }


def build_report(runs: list[dict], report_date: str, generated_at_msk: datetime,
                 backend_label: str = "—") -> tuple[str, str, str]:
    """Build (subject, html, text). Pure — no I/O — so it is easy to test.
    `backend_label` is the result of the internal /health check (see
    check_backend); the report never claims the public site is reachable."""
    s = _summarize(runs)
    generated = generated_at_msk.strftime("%Y-%m-%d %H:%M МСК")
    subject = f"HeadInspect — отчёт за {report_date}: проверок {s['total']}, сайтов {s['sites']}"

    rows = sorted(runs, key=lambda r: r.get("started_at") or r.get("created_at") or "")

    # --- plain-text alternative ------------------------------------------
    text_lines = [
        f"HeadInspect — ежедневный отчёт",
        f"Отчётный период: {report_date} (00:00–24:00 МСК)",
        f"Сформирован: {generated}",
        f"API (внутренняя проверка /health): {backend_label}",
        "",
        f"Запущено проверок: {s['total']}",
        f"Уникальных сайтов: {s['sites']}",
        f"Завершено: {s['completed']} · Неудачно: {s['failed']} · Незавершено: {s['unfinished']}",
        "",
    ]
    if not runs:
        text_lines.append("Сегодня проверок не было. Сервис работает.")
    else:
        text_lines.append("Домен | Начало (МСК) | Тип | Статус")
        for r in rows:
            text_lines.append(
                f"{r['site'] or '—'} | {_msk_time(r.get('started_at') or r.get('created_at'))} "
                f"| {_SOURCE_LABELS.get(r['source'], r['source'] or '—')} "
                f"| {_STATUS_LABELS.get(r['status'], r['status'] or '—')}"
            )
    text = "\n".join(text_lines)

    # --- HTML -------------------------------------------------------------
    def td(value: str) -> str:
        return f'<td style="padding:6px 10px;border-bottom:1px solid #e2e8f0;">{escape(value)}</td>'

    if not runs:
        body = (
            '<p style="margin:18px 0;padding:14px 16px;background:#f1f5f9;border-radius:8px;'
            'color:#334155;font-size:14px;">Сегодня проверок не было. Сервис работает.</p>'
        )
    else:
        table_rows = "".join(
            "<tr>"
            + td(r["site"] or "—")
            + td(_msk_time(r.get("started_at") or r.get("created_at")))
            + td(_SOURCE_LABELS.get(r["source"], r["source"] or "—"))
            + td(_STATUS_LABELS.get(r["status"], r["status"] or "—"))
            + "</tr>"
            for r in rows
        )
        body = (
            '<table style="border-collapse:collapse;width:100%;font-size:13px;color:#1e293b;">'
            '<thead><tr>'
            '<th style="text-align:left;padding:6px 10px;border-bottom:2px solid #cbd5e1;">Домен</th>'
            '<th style="text-align:left;padding:6px 10px;border-bottom:2px solid #cbd5e1;">Начало (МСК)</th>'
            '<th style="text-align:left;padding:6px 10px;border-bottom:2px solid #cbd5e1;">Тип</th>'
            '<th style="text-align:left;padding:6px 10px;border-bottom:2px solid #cbd5e1;">Статус</th>'
            '</tr></thead><tbody>' + table_rows + '</tbody></table>'
        )

    def stat(label: str, value: int) -> str:
        return (
            '<td style="padding:10px 14px;background:#f8fafc;border-radius:8px;text-align:center;">'
            f'<div style="font-size:22px;font-weight:700;color:#0f172a;">{value}</div>'
            f'<div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.04em;">{escape(label)}</div>'
            '</td>'
        )

    html = (
        '<!doctype html><html lang="ru"><body style="margin:0;background:#eef2f7;'
        'font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;">'
        '<div style="max-width:720px;margin:0 auto;padding:24px;">'
        '<div style="background:#fff;border-radius:14px;padding:24px 26px;box-shadow:0 8px 30px rgba(15,23,42,.06);">'
        '<div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;">HeadInspect</div>'
        '<h1 style="margin:4px 0 2px;font-size:20px;color:#0f172a;">Ежедневный отчёт</h1>'
        f'<p style="margin:0;color:#475569;font-size:13px;">Отчётный период: <strong>{escape(report_date)}</strong> '
        '(00:00–24:00 МСК)</p>'
        f'<p style="margin:2px 0 0;color:#64748b;font-size:12px;">Сформирован: {escape(generated)} · '
        f'API (внутренняя проверка /health): {escape(backend_label)}</p>'
        '<table style="border-collapse:separate;border-spacing:8px;width:100%;margin:18px 0 6px;"><tr>'
        + stat("Проверок", s["total"]) + stat("Сайтов", s["sites"])
        + stat("Завершено", s["completed"]) + stat("Неудачно", s["failed"])
        + stat("Незавершено", s["unfinished"]) +
        '</tr></table>'
        + body +
        '<p style="margin:18px 0 0;color:#94a3b8;font-size:11px;">Служебный отчёт HeadInspect. '
        'Проверка API — внутренняя (/health внутри контейнера), а не внешний мониторинг '
        'доступности сайта. Персональные данные посетителей не собираются и не сохраняются.</p>'
        '</div></div></body></html>'
    )
    return subject, html, text


def _smtp_config() -> dict:
    return {
        "to": os.getenv("HEADINSPECT_REPORT_TO"),
        "host": os.getenv("HEADINSPECT_SMTP_HOST"),
        "port": int(os.getenv("HEADINSPECT_SMTP_PORT", "587")),
        "user": os.getenv("HEADINSPECT_SMTP_USER"),
        "password": os.getenv("HEADINSPECT_SMTP_PASSWORD"),
        "sender": os.getenv("HEADINSPECT_SMTP_FROM") or os.getenv("HEADINSPECT_SMTP_USER"),
        "tls": (os.getenv("HEADINSPECT_SMTP_TLS", "starttls") or "starttls").strip().lower(),
    }


def send_email(subject: str, html: str, text: str, config: dict | None = None) -> None:
    """Send the report. Raises on any failure (caller must not mark the date
    sent). Never logs the SMTP password."""
    cfg = config or _smtp_config()
    if not cfg["to"] or not cfg["host"] or not cfg["sender"]:
        raise RuntimeError("SMTP не настроен: заданы не все HEADINSPECT_REPORT_TO/SMTP_HOST/SMTP_FROM")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = cfg["sender"]
    message["To"] = cfg["to"]
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    if cfg["tls"] == "ssl":
        smtp = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=SMTP_TIMEOUT)
    else:
        smtp = smtplib.SMTP(cfg["host"], cfg["port"], timeout=SMTP_TIMEOUT)
    try:
        if cfg["tls"] == "starttls":
            smtp.starttls()
        if cfg["user"] and cfg["password"]:
            smtp.login(cfg["user"], cfg["password"])
        smtp.send_message(message)
    finally:
        try:
            smtp.quit()
        except Exception:
            pass


@contextmanager
def _single_run_lock():
    """Exclusive, non-blocking file lock so two concurrent invocations (double
    cron fire, or manual run during cron) can never send the same report
    twice. Yields True if acquired, False if another run holds it. Released on
    exit — so a failed send does not block a later retry."""
    lock_path = f"{stats.db_path()}.lock"
    handle = open(lock_path, "w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        try:
            handle.close()  # releases the flock
        except Exception:
            pass


def run_report(now_msk: datetime | None = None) -> int:
    if not stats.enabled():
        logger.info("daily_report: HEADINSPECT_STATS_DB не задан — отчёт отключён")
        return 0

    now_msk = now_msk or datetime.now(MSK)
    now_utc = now_msk.astimezone(timezone.utc)
    report_date, start_utc, end_utc = report_window(now_msk)

    with _single_run_lock() as acquired:
        if not acquired:
            logger.info("daily_report: другой экземпляр отчёта уже выполняется — пропуск")
            return 0

        stats.cleanup(now_utc - timedelta(days=STATS_RETENTION_DAYS))

        if stats.already_sent(report_date):
            logger.info("daily_report: отчёт за %s уже отправлен — пропуск", report_date)
            return 0

        runs = stats.runs_between(start_utc, end_utc)
        backend_label = check_backend()
        subject, html, text = build_report(runs, report_date, now_msk, backend_label)

        try:
            send_email(subject, html, text)
        except Exception as exc:  # never include the exception message (may leak config)
            logger.error(
                "daily_report: ошибка отправки (%s) — дата НЕ отмечена, можно повторить",
                type(exc).__name__,
            )
            return 1

        stats.mark_sent(report_date, now_utc)
        logger.info("daily_report: отправлен отчёт за %s (%d проверок)", report_date, len(runs))
        return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    sys.exit(run_report())


if __name__ == "__main__":
    main()

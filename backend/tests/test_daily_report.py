"""Daily email report (app/daily_report.py)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import daily_report as dr
from app import stats


def test_report_window_is_previous_moscow_day():
    now = datetime(2026, 10, 26, 0, 5, tzinfo=dr.MSK)  # 00:05 MSK
    report_date, start_utc, end_utc = dr.report_window(now)
    assert report_date == "2026-10-25"
    # 2026-10-25 00:00 MSK == 2026-10-24 21:00 UTC
    assert start_utc == datetime(2026, 10, 24, 21, 0, tzinfo=timezone.utc)
    assert end_utc == datetime(2026, 10, 25, 21, 0, tzinfo=timezone.utc)


def _runs():
    return [
        {"site": "a.ru", "source": "general", "status": "completed",
         "created_at": "2026-10-25T06:00:00+00:00", "started_at": "2026-10-25T06:00:01+00:00"},
        {"site": "a.ru", "source": "meta", "status": "failed",
         "created_at": "2026-10-25T07:00:00+00:00", "started_at": None},
        {"site": "b.ru", "source": "images", "status": "running",
         "created_at": "2026-10-25T20:30:00+00:00", "started_at": "2026-10-25T20:30:02+00:00"},
    ]


def test_build_report_counts_and_table():
    subject, html, text = dr.build_report(_runs(), "2026-10-25", datetime(2026, 10, 26, 0, 5, tzinfo=dr.MSK))
    assert "проверок 3" in subject and "сайтов 2" in subject
    # unique sites = 2, completed 1, failed 1, unfinished 1 (the running one)
    assert ">2<" in html  # sites stat tile
    assert "a.ru" in html and "b.ru" in html
    assert "Images" in html and "Общий аудит" in html
    assert "выполняется" in html  # running run shown with its live status
    assert "<html" in html.lower()
    assert "Домен" in text and "a.ru" in text


def test_build_report_empty_still_has_message():
    subject, html, text = dr.build_report([], "2026-10-25", datetime(2026, 10, 26, 0, 5, tzinfo=dr.MSK))
    assert "Сегодня проверок не было. Сервис работает." in html
    assert "Сегодня проверок не было. Сервис работает." in text
    assert "проверок 0" in subject


def test_report_mentions_no_personal_data():
    _, html, text = dr.build_report(_runs(), "2026-10-25", datetime(2026, 10, 26, 0, 5, tzinfo=dr.MSK))
    blob = html + text
    # The report body carries no personal data: no email addresses, and only
    # the site/type/status/time fields we passed in.
    assert "@" not in blob
    assert "User-Agent" not in blob and "user-agent" not in blob


def test_send_email_builds_multipart_and_logs_in(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"], sent["port"] = host, port

        def starttls(self):
            sent["starttls"] = True

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, message):
            sent["message"] = message

        def quit(self):
            sent["quit"] = True

    monkeypatch.setattr(dr.smtplib, "SMTP", FakeSMTP)
    cfg = {"to": "owner@example.ru", "host": "smtp.example.ru", "port": 587,
           "user": "u", "password": "secret", "sender": "bot@example.ru", "tls": "starttls"}
    dr.send_email("Тема", "<b>html</b>", "text", config=cfg)

    msg = sent["message"]
    assert msg["To"] == "owner@example.ru"
    assert msg["Subject"] == "Тема"
    assert msg.get_content_type() == "multipart/alternative"
    assert sent["starttls"] is True
    assert sent["login"] == ("u", "secret")
    assert sent["quit"] is True


def test_send_email_requires_config():
    with pytest.raises(RuntimeError):
        dr.send_email("S", "h", "t", config={"to": None, "host": None, "port": 587,
                                              "user": None, "password": None, "sender": None, "tls": "starttls"})


def test_run_report_sends_once_then_dedups(monkeypatch, tmp_path):
    monkeypatch.setenv("HEADINSPECT_STATS_DB", str(tmp_path / "s.db"))
    now = datetime(2026, 10, 26, 0, 5, tzinfo=dr.MSK)
    created = datetime(2026, 10, 25, 9, 0, tzinfo=dr.MSK).astimezone(timezone.utc)
    stats.record_created("j1", "a.ru", "meta", created)
    stats.record_status("j1", "completed", created)

    calls = []
    monkeypatch.setattr(dr, "send_email", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(dr, "check_backend", lambda: "доступен")
    assert dr.run_report(now) == 0
    assert len(calls) == 1
    # Same report date again -> no second send.
    assert dr.run_report(now) == 0
    assert len(calls) == 1


def test_run_report_failure_leaves_date_unmarked_for_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("HEADINSPECT_STATS_DB", str(tmp_path / "s.db"))
    now = datetime(2026, 10, 26, 0, 5, tzinfo=dr.MSK)

    monkeypatch.setattr(dr, "check_backend", lambda: "доступен")

    def boom(*a, **k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(dr, "send_email", boom)
    assert dr.run_report(now) == 1  # failed

    # Retry succeeds because the date was never marked sent.
    calls = []
    monkeypatch.setattr(dr, "send_email", lambda *a, **k: calls.append(1))
    assert dr.run_report(now) == 0
    assert len(calls) == 1


def test_run_report_disabled_without_db(monkeypatch):
    monkeypatch.delenv("HEADINSPECT_STATS_DB", raising=False)
    assert dr.run_report(datetime(2026, 10, 26, 0, 5, tzinfo=dr.MSK)) == 0


def test_run_report_skips_when_lock_already_held(monkeypatch, tmp_path):
    import fcntl
    db = tmp_path / "s.db"
    monkeypatch.setenv("HEADINSPECT_STATS_DB", str(db))
    stats.init_db()
    now = datetime(2026, 10, 26, 0, 5, tzinfo=dr.MSK)

    # A concurrent run holds the lock.
    held = open(str(db) + ".lock", "w")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        calls = []
        monkeypatch.setattr(dr, "send_email", lambda *a, **k: calls.append(1))
        monkeypatch.setattr(dr, "check_backend", lambda: "доступен")
        assert dr.run_report(now) == 0
        assert calls == []  # no duplicate send while another run holds the lock
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()


def test_check_backend(monkeypatch):
    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def getcode(self):
            return 200

    monkeypatch.setattr(dr.urllib.request, "urlopen", lambda *a, **k: FakeResp())
    assert dr.check_backend() == "доступен"

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(dr.urllib.request, "urlopen", boom)
    assert dr.check_backend() == "не отвечает"


def test_build_report_backend_label_and_interrupted_status():
    runs = [{"site": "a.ru", "source": "general", "status": "interrupted",
             "created_at": "2026-10-25T06:00:00+00:00", "started_at": "2026-10-25T06:00:01+00:00"}]
    _, html, text = dr.build_report(runs, "2026-10-25", datetime(2026, 10, 26, 0, 5, tzinfo=dr.MSK),
                                    backend_label="доступен")
    assert "прервана" in html and "прервана" in text  # interrupted run shown clearly
    assert "доступен" in html and "доступен" in text   # backend label surfaced
    assert "/health" in html                            # framed as an internal check

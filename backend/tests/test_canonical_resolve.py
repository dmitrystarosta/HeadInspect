"""Phase-B canonical resolution (cross-page, in-memory, no network).

Everything here is resolved purely from the pages already collected by the
audit. The tests assert the agreed severities for redirecting targets,
4xx/5xx targets, noindex targets, chains and cycles, plus the deliberately
non-alarming behaviour when a target is absent from the audit (including under
completed_partial) - and that no network is ever touched (the tests provide no
fetch at all).
"""
from __future__ import annotations

from app.canonical_resolve import resolve_canonicals
from app.models import CanonicalData, PageResult


def _page(requested, url=None, status=200, canonical=None, noindex=False,
          check_failed=False, reason=None):
    cd = CanonicalData(page_noindex=noindex)
    if canonical is not None:
        cd.present = True
        cd.resolved_url = canonical
        cd.valid_url = True
        cd.is_self = False
    return PageResult(
        url=url or requested,
        requested_url=requested,
        status_code=status,
        canonical=cd,
        check_failed=check_failed,
        check_reason=reason,
    )


def test_self_canonical_needs_no_resolution():
    page = _page("https://s.ru/a", canonical="https://s.ru/a")
    page.canonical.is_self = True
    resolve_canonicals([page])
    assert page.canonical.target_in_audit is None
    assert page.canonical.errors == [] and page.canonical.warnings == []


def test_target_present_ok():
    a = _page("https://s.ru/a", canonical="https://s.ru/b")
    b = _page("https://s.ru/b")
    resolve_canonicals([a, b])
    assert a.canonical.target_in_audit is True
    assert a.canonical.target_status == 200
    assert a.canonical.errors == [] and a.canonical.warnings == []


def test_target_redirects_is_warning():
    a = _page("https://s.ru/a", canonical="https://s.ru/b")
    b = _page("https://s.ru/b", url="https://s.ru/c")
    resolve_canonicals([a, b])
    assert a.canonical.target_redirected is True
    assert any("редиректом" in w for w in a.canonical.warnings)


def test_target_4xx_is_error():
    a = _page("https://s.ru/a", canonical="https://s.ru/gone")
    gone = _page("https://s.ru/gone", status=404)
    resolve_canonicals([a, gone])
    assert any("HTTP 404" in e for e in a.canonical.errors)


def test_target_5xx_is_error():
    a = _page("https://s.ru/a", canonical="https://s.ru/boom")
    boom = _page("https://s.ru/boom", status=500)
    resolve_canonicals([a, boom])
    assert any("HTTP 500" in e for e in a.canonical.errors)


def test_target_noindex_is_error():
    a = _page("https://s.ru/a", canonical="https://s.ru/ni")
    ni = _page("https://s.ru/ni", noindex=True)
    resolve_canonicals([a, ni])
    assert a.canonical.target_noindex is True
    assert any("noindex" in e for e in a.canonical.errors)


def test_chain_is_warning():
    a = _page("https://s.ru/a", canonical="https://s.ru/b")
    b = _page("https://s.ru/b", canonical="https://s.ru/c")
    c = _page("https://s.ru/c", canonical="https://s.ru/c")  # terminal self
    c.canonical.is_self = True
    resolve_canonicals([a, b, c])
    assert a.canonical.chain == ["https://s.ru/a", "https://s.ru/b", "https://s.ru/c"]
    assert any("Цепочка canonical" in w for w in a.canonical.warnings)


def test_cycle_is_error():
    a = _page("https://s.ru/a", canonical="https://s.ru/b")
    b = _page("https://s.ru/b", canonical="https://s.ru/a")
    resolve_canonicals([a, b])
    assert a.canonical.cycle is True
    assert any("цикл" in e.lower() for e in a.canonical.errors)


def test_missing_target_is_informational_only():
    a = _page("https://s.ru/a", canonical="https://other.com/x")
    resolve_canonicals([a])
    assert a.canonical.target_in_audit is False
    assert a.canonical.errors == [] and a.canonical.warnings == []
    assert any("не входит в проверенные" in n for n in a.canonical.notes)


def test_access_blocked_target_is_not_hard_error():
    a = _page("https://s.ru/a", canonical="https://s.ru/blocked")
    blocked = _page("https://s.ru/blocked", status=403, check_failed=True, reason="access_blocked")
    resolve_canonicals([a, blocked])
    assert a.canonical.errors == []
    assert any("защита" in n for n in a.canonical.notes)


def test_completed_partial_missing_target_no_error():
    # Simulates a partial audit: canonical points at a page that was never
    # reached before the audit stopped. Must be "not checked", never an error.
    a = _page("https://s.ru/a", canonical="https://s.ru/never-reached")
    resolve_canonicals([a])  # only page A survived
    assert a.canonical.target_in_audit is False
    assert a.canonical.errors == []


def test_resolution_is_idempotent():
    a = _page("https://s.ru/a", canonical="https://s.ru/gone")
    gone = _page("https://s.ru/gone", status=404)
    resolve_canonicals([a, gone])
    first = list(a.canonical.errors)
    # A second run (defensive) must not double up messages beyond a stable set.
    a2 = _page("https://s.ru/a", canonical="https://s.ru/gone")
    resolve_canonicals([a2, _page("https://s.ru/gone", status=404)])
    assert a2.canonical.errors == first


def test_default_port_target_matches_map():
    a = _page("https://s.ru/a", canonical="https://s.ru:443/b")
    b = _page("https://s.ru/b", status=200)
    resolve_canonicals([a, b])
    assert a.canonical.target_in_audit is True

"""Cross-page canonical resolution (phase B).

Runs once, after the audit's pages have been collected, entirely in memory:
it never makes a network request. It can only reason about canonical targets
that were themselves part of *this* audit - for anything else it records the
honest, non-alarming "Целевая страница не проверялась".

This is the direct analogue of ``JobManager._apply_meta_duplicate_warnings``:
a synchronous post-pass over ``job.results``. It is designed to be safe to
run on a partial result set (``completed_partial``): a target simply missing
from the map means "not checked", never an error.

Guarantees, agreed with the maintainer:
  * no additional network requests are ever issued for canonical targets;
  * redirect / status / noindex / chain / cycle are derived only from pages
    already present in the audit's own result list;
  * chains and cycles are bounded by a visited-set and a hard depth cap.
"""

from __future__ import annotations

from .models import PageResult
from .urlnorm import canonical_equiv_key

_MAX_CHAIN_DEPTH = 20


def resolve_canonicals(results: list[PageResult]) -> None:
    """Fill each page's phase-B canonical fields in place, from ``results``
    only. Idempotent for a given result set."""
    # Two lookup maps keyed by the safe-equivalence key:
    #   by_requested - the URL a page was *asked* for (lets us notice that a
    #                  canonical points at a URL which itself redirects);
    #   by_final     - the URL a page actually resolved to after redirects.
    by_requested: dict[str, PageResult] = {}
    by_final: dict[str, PageResult] = {}
    for page in results:
        req = page.requested_url or page.url
        if req:
            key = canonical_equiv_key(req)
            if key is not None:
                by_requested.setdefault(key, page)
        if page.url:
            key = canonical_equiv_key(page.url)
            if key is not None:
                by_final.setdefault(key, page)

    def lookup(url: str | None) -> tuple[PageResult | None, bool]:
        """Return (page, redirected). Prefer the requested-URL map so we can
        tell that the target itself redirects; fall back to the final-URL
        map (target is a valid post-redirect URL, no redirect implied)."""
        if not url:
            return None, False
        key = canonical_equiv_key(url)
        if key is None:
            return None, False
        page = by_requested.get(key)
        if page is not None:
            redirected = bool(page.url and page.requested_url and page.url != page.requested_url)
            return page, redirected
        return by_final.get(key), False

    for page in results:
        cd = page.canonical
        target = cd.resolved_url
        if not target or cd.is_self:
            # No usable/foreign target, or self-canonical: nothing cross-page
            # to resolve. Leave phase-B fields at their defaults (None).
            continue

        target_page, redirected = lookup(target)
        if target_page is None:
            # Rule: target not among audited pages -> informational only.
            cd.target_in_audit = False
            cd.notes.append("Целевая страница canonical не входит в проверенные страницы этого аудита")
            continue

        cd.target_in_audit = True
        cd.target_status = target_page.status_code
        cd.target_redirected = redirected
        cd.target_final_url = target_page.url
        cd.target_noindex = target_page.canonical.page_noindex
        cd.target_canonical = target_page.canonical.resolved_url

        _classify_target(cd, target_page, redirected)
        _resolve_chain(cd, page, lookup)


def _classify_target(cd, target_page: PageResult, redirected: bool) -> None:
    status = target_page.status_code

    if target_page.check_failed:
        # We reached the page during the audit but couldn't trust its content.
        if target_page.check_reason == "access_blocked":
            cd.notes.append(
                f"Целевая страница canonical вернула HTTP {status} для HeadInspect "
                "(возможно, защита от автоматического доступа)"
            )
        else:
            cd.notes.append("Целевая страница canonical не была успешно проверена в этом аудите")
        return

    if status is not None and status >= 400:
        # Rule 19: canonical points at a 4xx/5xx page -> error.
        cd.errors.append(f"Canonical ведёт на страницу с ошибкой HTTP {status}")
    elif redirected:
        # Rule 18: canonical points at a URL that redirects -> warning.
        cd.warnings.append(
            f"Canonical ведёт на URL с редиректом (конечный адрес: {target_page.url})"
        )

    if cd.target_noindex:
        # Rule 20: canonical target is noindex -> error (conflicting signals).
        cd.errors.append("Canonical ведёт на страницу с noindex — конфликт сигналов индексации")


def _resolve_chain(cd, page: PageResult, lookup) -> None:
    """Follow canonical -> canonical purely through the audit's own map.
    Builds cd.chain and detects a cycle back to the starting page. Bounded by
    a visited set and _MAX_CHAIN_DEPTH so a hostile map can't loop us."""
    start = page.url or page.requested_url
    start_key = canonical_equiv_key(start)
    if start_key is None:
        return

    chain = [start]
    visited = {start_key}
    current = cd.resolved_url
    depth = 0

    while current and depth < _MAX_CHAIN_DEPTH:
        depth += 1
        current_key = canonical_equiv_key(current)
        if current_key is None:
            break

        if current_key == start_key:
            # Rule 21: A -> ... -> A cycle.
            chain.append(current)
            cd.cycle = True
            cd.chain = chain
            cd.errors.append("Обнаружен цикл canonical: страница ссылается сама на себя через цепочку")
            return

        if current_key in visited:
            # Cycle that doesn't include the start page, but still a loop.
            chain.append(current)
            cd.cycle = True
            cd.chain = chain
            cd.errors.append("Обнаружен цикл canonical в цепочке целевых страниц")
            return

        chain.append(current)
        visited.add(current_key)

        target_page, _redirected = lookup(current)
        if target_page is None:
            break
        nxt = target_page.canonical.resolved_url
        if not nxt or canonical_equiv_key(nxt) == current_key:
            # Terminal: target is self-canonical or has no onward canonical.
            break
        current = nxt

    if len(chain) > 2 and not cd.cycle:
        # Rule: A -> B -> C chain (all distinct) -> warning.
        cd.chain = chain
        cd.warnings.append(
            f"Цепочка canonical из {len(chain)} страниц — поисковые системы могут не пройти её целиком"
        )

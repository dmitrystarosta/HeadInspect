"""Per-page canonical analysis (phase A) - HTML + response headers only.

Everything here is computed from data the ordinary audit already fetched for
this one page: its parsed HTML (``MetadataParser``) and its HTTP response
headers. No network request is ever made from this module. Cross-page facts
(does the target redirect, is it 4xx/5xx, is it noindex, chains, cycles) are
resolved separately, after the whole audit, by ``canonical_resolve`` - and
only from pages that were actually part of the audit.

Severity rules are exactly the ones agreed with the maintainer; see the
inline comments at each decision point. The guiding principle: HeadInspect
must not flag as an error/warning anything the standard permits (cross-domain
canonical, relative canonical, query parameters, a deliberate www/non-www or
default-port choice) - those are surfaced informationally in ``notes``.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from ..htmlmeta import MetadataParser
from ..models import CanonicalData
from ..urlnorm import canonical_equiv_key, urls_equivalent

# One Link header value entry: <url>; rel="canonical" (rel may be unquoted,
# and other params may appear before/after). We only care about entries whose
# rel token set contains "canonical".
_LINK_ENTRY_RE = re.compile(r"<([^>]*)>\s*;\s*(.*)", re.DOTALL)
_REL_RE = re.compile(r"""rel\s*=\s*(?:"([^"]*)"|'([^']*)'|([^;,\s]+))""", re.IGNORECASE)


def _parse_link_header_canonicals(headers) -> list[str]:
    """Extract every rel=canonical target URL from the HTTP ``Link`` header(s).
    ``headers`` is anything with a case-insensitive ``.get`` (httpx.Headers or
    a plain dict in tests). Multiple Link headers arrive comma-joined."""
    if headers is None or not hasattr(headers, "get"):
        return []
    raw = headers.get("link")
    if not raw:
        return []

    results: list[str] = []
    for entry in _split_link_entries(raw):
        m = _LINK_ENTRY_RE.match(entry.strip())
        if not m:
            continue
        url, params = m.group(1).strip(), m.group(2)
        rel_match = _REL_RE.search(params)
        if not rel_match:
            continue
        rel_value = rel_match.group(1) or rel_match.group(2) or rel_match.group(3) or ""
        if "canonical" in rel_value.lower().split():
            results.append(url)
    return results


def _split_link_entries(raw: str) -> list[str]:
    entries: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in raw:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            entries.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        entries.append("".join(current))
    return entries


def _directives_have_noindex(value: str) -> bool:
    directives = {
        part.strip().lower()
        for part in value.replace(";", ",").split(",")
        if part.strip()
    }
    return "noindex" in directives or "none" in directives


def _x_robots_noindex(headers) -> bool:
    if headers is None or not hasattr(headers, "get"):
        return False
    value = headers.get("x-robots-tag")
    return bool(value) and _directives_have_noindex(value)


def _page_noindex(parser: MetadataParser, headers) -> bool:
    for value in parser.meta_values("robots"):
        if value and _directives_have_noindex(value):
            return True
    return _x_robots_noindex(headers)


def _is_relative(href: str) -> bool:
    # Absolute means it carries its own scheme (https:, http:). Everything
    # else - "/path", "path", "//host/path" - needs a base to resolve.
    try:
        return urlsplit(href).scheme == ""
    except ValueError:
        return True


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def analyze_canonical(
    parser: MetadataParser,
    headers,
    requested_url: str,
    final_url: str,
) -> CanonicalData:
    html_hrefs = list(parser.canonical_hrefs)
    header_hrefs = _parse_link_header_canonicals(headers)
    html_count = len(html_hrefs)
    header_count = len(header_hrefs)
    present = html_count > 0 or header_count > 0

    # NOTE: pydantic copies lists passed at construction time, so once `data`
    # exists we mutate data.errors/warnings/notes DIRECTLY - never local lists.
    data = CanonicalData(
        present=present,
        html_count=html_count,
        header_count=header_count,
        raw_hrefs=list(html_hrefs + header_hrefs),
        page_noindex=_page_noindex(parser, headers),
    )
    if html_hrefs:
        data.raw_href = html_hrefs[0]
    elif header_hrefs:
        data.raw_href = header_hrefs[0]

    # Rule 1: no canonical at all -> warning (HeadInspect is an auditor; a
    # green "all good" on a page with no canonical would mislead).
    if not present:
        data.warnings.append("Canonical не указан")
        return data

    # Resolution base: a valid <base href> changes how a *relative* canonical
    # resolves (standard HTML URL resolution). The base itself may be
    # relative, so resolve it against the page's own final URL first.
    base_url = final_url
    base_available = False
    if parser.base_href:
        resolved_base = urljoin(final_url, parser.base_href)
        if canonical_equiv_key(resolved_base) is not None:
            base_url = resolved_base
            base_available = True

    signals: list[tuple[str, str, str | None, bool]] = []
    any_empty = False
    any_invalid = False
    base_used = False

    def _add(source: str, raw: str) -> None:
        nonlocal any_empty, any_invalid, base_used
        if raw == "":
            any_empty = True
            signals.append((source, raw, None, False))
            return
        rel = _is_relative(raw)
        resolved = urljoin(base_url if base_available else final_url, raw)
        if rel and base_available:
            base_used = True
        if canonical_equiv_key(resolved) is None:
            any_invalid = True
            signals.append((source, raw, None, rel))
            return
        signals.append((source, raw, resolved, rel))

    for h in html_hrefs:
        _add("html", h)
    for h in header_hrefs:
        _add("header", h)

    data.empty_href = any_empty
    data.base_href_used = base_used

    if any_empty:
        data.errors.append("Пустой href у canonical: тег присутствует, но адрес не указан")
    if any_invalid:
        bad = next((raw for src, raw, res, rel in signals if raw and res is None), "")
        data.errors.append(f"Некорректный URL в canonical: {bad}")

    valid_signals = [(src, raw, res, rel) for (src, raw, res, rel) in signals if res is not None]

    distinct_keys: dict[str, str] = {}
    for _src, _raw, res, _rel in valid_signals:
        key = canonical_equiv_key(res)
        if key is not None and key not in distinct_keys:
            distinct_keys[key] = res
    data.count = len(distinct_keys)

    html_valid = [s for s in valid_signals if s[0] == "html"]
    header_valid = [s for s in valid_signals if s[0] == "header"]
    if html_valid and header_valid:
        data.source = "both"
    elif header_valid:
        data.source = "header"
    elif html_valid:
        data.source = "html"
    else:
        data.source = "none"

    if not valid_signals:
        # Present but nothing usable (only empty/invalid) - already errored.
        return data

    if len(distinct_keys) > 1:
        # Rule 7 / 8: several *different* canonical signals -> error, whether
        # html-vs-html or html-vs-HTTP-Link.
        data.conflict = True
        if html_valid and header_valid and not _sources_agree(html_valid, header_valid):
            data.errors.append(
                "Конфликт canonical: HTML <link rel=\"canonical\"> и HTTP-заголовок Link "
                "указывают разные адреса"
            )
        else:
            data.errors.append(f"Найдено несколько разных canonical: {len(distinct_keys)}")
        chosen = valid_signals[0][2]
    else:
        chosen = next(iter(distinct_keys.values()))
        if len(valid_signals) > 1:
            # Rule 7 / 8: several *identical* signals -> warning (duplication),
            # with both sources shown in details.
            if data.source == "both":
                data.warnings.append(
                    "Canonical продублирован: один и тот же адрес указан и в HTML, и в HTTP-заголовке Link"
                )
                data.notes.append("Источник: HTML <link> и HTTP-заголовок Link")
            else:
                data.warnings.append(f"Canonical продублирован: {len(valid_signals)} одинаковых сигнала")

    data.resolved_url = chosen
    data.is_relative = any(rel for (_src, _raw, res, rel) in valid_signals if res == chosen)
    data.valid_url = True

    _classify_relationship(data, chosen, final_url)
    return data


def _sources_agree(html_valid, header_valid) -> bool:
    html_keys = {canonical_equiv_key(res) for (_s, _r, res, _rel) in html_valid}
    header_keys = {canonical_equiv_key(res) for (_s, _r, res, _rel) in header_valid}
    return html_keys == header_keys


def _classify_relationship(data: CanonicalData, chosen: str, final_url: str) -> None:
    chosen_parts = urlsplit(chosen)
    page_parts = urlsplit(final_url)

    chosen_host = (chosen_parts.hostname or "").lower()
    page_host = (page_parts.hostname or "").lower()

    data.has_fragment = bool(chosen_parts.fragment)
    data.has_query = bool(chosen_parts.query)
    data.same_site = chosen_host == page_host
    data.cross_domain = not data.same_site
    data.scheme_mismatch = (chosen_parts.scheme or "").lower() != (page_parts.scheme or "").lower()
    data.host_variant_mismatch = (
        not data.same_site and _strip_www(chosen_host) == _strip_www(page_host)
    )
    data.is_self = urls_equivalent(chosen, final_url)

    if data.is_relative:
        data.notes.append(f"Относительный canonical: {data.raw_href} \u2192 {chosen}")

    if data.is_self:
        pass  # Rule 9: self-referencing canonical -> OK.
    elif data.cross_domain:
        # Rule 4: cross-domain canonical is legitimate -> info only.
        data.notes.append(f"Canonical \u0432\u0435\u0434\u0451\u0442 \u043d\u0430 \u0434\u0440\u0443\u0433\u043e\u0439 \u0434\u043e\u043c\u0435\u043d: {chosen_host}")
    else:
        # Rule 10 (same-site, points elsewhere) -> info; may escalate in the
        # resolution pass if the target itself has a problem.
        data.notes.append(f"Canonical \u0443\u043a\u0430\u0437\u044b\u0432\u0430\u0435\u0442 \u043d\u0430 \u0434\u0440\u0443\u0433\u0443\u044e \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0443 \u0441\u0430\u0439\u0442\u0430: {chosen}")

    if data.host_variant_mismatch:
        data.notes.append("Canonical \u0443\u043a\u0430\u0437\u044b\u0432\u0430\u0435\u0442 \u043d\u0430 \u0434\u0440\u0443\u0433\u043e\u0439 \u0432\u0430\u0440\u0438\u0430\u043d\u0442 \u0445\u043e\u0441\u0442\u0430 (www/non-www)")

    if data.scheme_mismatch:
        # Rule 12: http<->https is a valid URL but almost always a mistake.
        data.warnings.append(
            f"Canonical \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0435\u0442 \u0441\u0445\u0435\u043c\u0443 {chosen_parts.scheme}, \u043e\u0442\u043b\u0438\u0447\u043d\u0443\u044e \u043e\u0442 \u0441\u0445\u0435\u043c\u044b \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u044b"
        )

    if data.has_fragment:
        # Rule 13: a fragment is ignored for canonicalization.
        data.warnings.append("Canonical \u0441\u043e\u0434\u0435\u0440\u0436\u0438\u0442 \u0444\u0440\u0430\u0433\u043c\u0435\u043d\u0442 (#...), \u043e\u043d \u043d\u0435 \u0443\u0447\u0430\u0441\u0442\u0432\u0443\u0435\u0442 \u0432 \u043a\u0430\u043d\u043e\u043d\u0438\u0437\u0430\u0446\u0438\u0438")

    if data.has_query:
        # Rule 5: query parameters can be perfectly correct -> info only.
        data.notes.append("Canonical \u0441\u043e\u0434\u0435\u0440\u0436\u0438\u0442 query-\u043f\u0430\u0440\u0430\u043c\u0435\u0442\u0440\u044b")

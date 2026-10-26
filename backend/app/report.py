"""Excel (.xlsx) export of a finished audit.

Produces a single workbook for one audit job (see main.py's
GET /api/audits/{job_id}/report.xlsx):

  * «Сводка»     - key/value overview: site, date, status, page counts and
                   per-module error/warning totals (+ partial_reason when the
                   audit finished partially);
  * one sheet per working content module (Open Graph, Meta, Canonical,
    Schema) - one row per successfully-checked final page, deduplicated the
    same way the UI deduplicates (uniqueContentPages), plus that module's
    own errors/warnings;
  * «Sitemap»    - one row per discovered URL *including* redirects and
                   pages that could not be checked (availability view,
                   matching the Sitemap module's own semantics);
  * «Проблемы»   - a flat list of every error and warning across all
                   modules, one row each: URL, module, kind, message.

This module is read-only over the job: it never fetches anything, never
touches the network, and builds purely from results already collected by the
audit. openpyxl is the only new dependency. Nothing here changes existing
API contracts - it is an additive, separate endpoint.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import urlsplit

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .jobs import Job
from .models import PageResult


# Human-readable status labels. Only completed / completed_partial ever reach
# the report builder (the route gates the rest), but the map is exhaustive so
# a future status can never render as a raw english token.
_STATUS_LABELS = {
    "completed": "Завершён",
    "completed_partial": "Завершён частично",
    "queued": "В очереди",
    "discovering": "Поиск страниц",
    "running": "Выполняется",
    "failed": "Ошибка",
}

_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill("solid", fgColor="0F172A")
_WRAP = Alignment(vertical="top", wrap_text=True)
_TOP = Alignment(vertical="top")

# Leading characters that spreadsheet apps may interpret as the start of a
# formula. Any site-controlled string beginning with one of these (or a
# control char) is written as literal text, never as an executable formula -
# Excel/CSV formula-injection protection. openpyxl itself would otherwise turn
# a value like "=cmd|..." into a real formula cell.
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r", "\n")

# Lines like "HTTP 404" are availability facts injected into a page's
# top-level errors/warnings by the fetch step (audit._fetch_and_analyze), not
# Open Graph findings. They belong to the Sitemap/«Доступность» view, so they
# are kept out of the Open Graph sheet and its issue rows.
_HTTP_LINE = re.compile(r"^HTTP \d+$")


def _neutralize_formula(value: str) -> str:
    """Prefix a single quote so a site-controlled string can never be
    interpreted as a formula. Excel hides the leading apostrophe and shows the
    original text; the value itself is unchanged for the reader."""
    return "'" + value if value and value[0] in _FORMULA_LEADS else value


def _harden_workbook(wb: Workbook) -> None:
    """Final pass over every cell in every sheet: any string value that could
    be read as a formula is rewritten as literal text. Centralised so no
    individual sheet builder can forget it."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    hardened = _neutralize_formula(cell.value)
                    if hardened != cell.value:
                        cell.value = hardened


def _og_issues(page: PageResult) -> tuple[list[str], list[str]]:
    """Open Graph-specific errors/warnings only: the page's top-level lists
    with the generic 'HTTP NNN' availability lines filtered out."""
    errors = [e for e in page.errors if not _HTTP_LINE.match(e)]
    warnings = [w for w in page.warnings if not _HTTP_LINE.match(w)]
    return errors, warnings


def _bool_label(value: bool | None) -> str:
    if value is True:
        return "да"
    if value is False:
        return "нет"
    return "—"


def _or_dash(value) -> str:
    if value is None:
        return "—"
    text = str(value)
    return text if text != "" else "—"


def _join_issues(items: list[str]) -> str:
    return "\n".join(items) if items else ""


def _report_host(job: Job) -> str:
    source = job.normalized_url or job.requested_url or ""
    try:
        host = urlsplit(source).hostname
    except ValueError:
        host = None
    return host or "site"


def report_filename(job: Job) -> str:
    """Stable, human-friendly download name: headinspect-<host>-<date>.xlsx."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"headinspect-{_report_host(job)}-{date}.xlsx"


def _unique_content_pages(results: list[PageResult]) -> list[PageResult]:
    """Mirror of common.js uniqueContentPages: show each *final* page once,
    preferring the page reached directly over one reached only via a redirect;
    check_failed pages (and pages with no final url) are never merged. Kept in
    sync with the frontend so the Excel content sheets match what the UI shows.
    """
    by_final: dict[str, PageResult] = {}
    without_final: list[PageResult] = []

    for page in results:
        if page.check_failed or not page.url:
            without_final.append(page)
            continue
        existing = by_final.get(page.url)
        is_direct = not page.requested_url or page.requested_url == page.url
        existing_is_direct = existing is not None and (
            not existing.requested_url or existing.requested_url == existing.url
        )
        if existing is None or (is_direct and not existing_is_direct):
            by_final[page.url] = page

    return [*by_final.values(), *without_final]


def _style_header(ws: Worksheet, ncols: int) -> None:
    for col in range(1, ncols + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _TOP
    ws.freeze_panes = "A2"


def _autosize(ws: Worksheet, widths: list[int]) -> None:
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width


def _write_rows(ws: Worksheet, header: list[str], rows: list[list], widths: list[int]) -> None:
    ws.append(header)
    for row in rows:
        ws.append(row)
    _style_header(ws, len(header))
    _autosize(ws, widths)
    # Wrap the long free-text issue columns so a cell with several messages
    # stays readable instead of overflowing into neighbours.
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = _WRAP


def _http_label(page: PageResult) -> str:
    return str(page.status_code) if page.status_code is not None else "—"


# --- per-module sheet builders ------------------------------------------------

def _sheet_open_graph(ws: Worksheet, content_pages: list[PageResult]) -> None:
    header = ["URL", "HTTP", "og:title", "og:description", "og:type",
              "og:url", "og:image", "og:image доступен", "Ошибки", "Предупреждения"]
    rows = []
    for page in content_pages:
        if page.check_failed:
            continue
        og = page.open_graph
        og_errors, og_warnings = _og_issues(page)
        rows.append([
            _or_dash(page.url), _http_label(page),
            _or_dash(og.title), _or_dash(og.description), _or_dash(og.type),
            _or_dash(og.url), _or_dash(og.image), _bool_label(og.image_accessible),
            _join_issues(og_errors), _join_issues(og_warnings),
        ])
    _write_rows(ws, header, rows, [46, 7, 28, 34, 12, 30, 40, 16, 40, 40])


def _sheet_meta(ws: Worksheet, content_pages: list[PageResult]) -> None:
    header = ["URL", "HTTP", "title", "description", "keywords", "robots",
              "viewport", "lang", "charset", "Ошибки", "Предупреждения"]
    rows = []
    for page in content_pages:
        if page.check_failed:
            continue
        m = page.meta
        rows.append([
            _or_dash(page.url), _http_label(page),
            _or_dash(m.title), _or_dash(m.description), _or_dash(m.keywords),
            _or_dash(m.robots), _or_dash(m.viewport), _or_dash(m.lang), _or_dash(m.charset),
            _join_issues(m.errors), _join_issues(m.warnings),
        ])
    _write_rows(ws, header, rows, [46, 7, 28, 34, 24, 16, 16, 8, 10, 40, 40])


def _sheet_canonical(ws: Worksheet, content_pages: list[PageResult]) -> None:
    header = ["URL", "HTTP", "Canonical", "Источник", "Ссылается на себя",
              "Кросс-домен", "Ошибки", "Предупреждения"]
    rows = []
    for page in content_pages:
        if page.check_failed:
            continue
        c = page.canonical
        rows.append([
            _or_dash(page.url), _http_label(page),
            _or_dash(c.resolved_url), _or_dash(c.source),
            _bool_label(c.is_self), _bool_label(c.cross_domain),
            _join_issues(c.errors), _join_issues(c.warnings),
        ])
    _write_rows(ws, header, rows, [46, 7, 46, 12, 18, 14, 40, 40])


def _sheet_schema(ws: Worksheet, content_pages: list[PageResult]) -> None:
    header = ["URL", "HTTP", "JSON-LD", "Типы", "Microdata типы",
              "Ошибки", "Предупреждения"]
    rows = []
    for page in content_pages:
        if page.check_failed:
            continue
        s = page.schema_data
        rows.append([
            _or_dash(page.url), _http_label(page),
            s.json_ld_count,
            _or_dash(", ".join(s.types)) if s.types else "—",
            _or_dash(", ".join(s.microdata_types)) if s.microdata_types else "—",
            _join_issues(s.errors), _join_issues(s.warnings),
        ])
    _write_rows(ws, header, rows, [46, 7, 10, 30, 30, 40, 40])


def _format_bytes(size: int | None) -> str:
    if size is None:
        return "—"
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} МБ"
    return f"{max(1, round(size / 1024))} КБ"


def _alt_label(item) -> str:
    # alt belongs to the <img> element, not to each srcset candidate URL, so a
    # srcset variant legitimately has no alt of its own - never shown as "нет".
    if item.source == "srcset":
        return "—"
    if not item.has_alt:
        return "нет"
    if item.alt_empty:
        return "(пусто)"
    return item.alt or ""


def _availability_label(item) -> str:
    """Human availability status, distinguishing a confirmed HTTP error from an
    image that simply was not checked (caps/time) or could not be determined."""
    if item.accessible is True:
        return "доступно"
    if item.accessible is False:
        return f"недоступно (HTTP {item.status_code})" if item.status_code else "недоступно"
    if not item.checked:
        return "не проверено"
    return "не удалось определить"


def _sheet_images(ws: Worksheet, content_pages: list[PageResult]) -> None:
    header = ["Страница", "URL изображения", "Источник", "alt (у <img>)", "Размеры (W×H)",
              "loading", "Формат", "Доступность", "HTTP", "Вес", "Повтор (стр.)", "Проблемы"]
    rows = []
    for page in content_pages:
        if page.check_failed:
            continue
        for item in page.images.items:
            dims = f"{item.width_attr}×{item.height_attr}" if (item.width_attr and item.height_attr) else "—"
            # The availability note (reason) is folded into the status label /
            # issues column, so don't double it as an error.
            issues = list(item.errors) + list(item.warnings)
            if item.availability_note and item.accessible is None:
                issues.append(item.availability_note)
            source_label = "srcset-вариант" if item.source == "srcset" else "img"
            rows.append([
                _or_dash(page.url),
                _or_dash(item.src or item.raw_src),
                source_label,
                _alt_label(item),
                dims,
                _or_dash(item.loading),
                _or_dash(item.format_guess),
                _availability_label(item),
                str(item.status_code) if item.status_code is not None else "—",
                _format_bytes(item.bytes),
                item.reuse_count,
                _join_issues(issues),
            ])
    _write_rows(ws, header, rows, [38, 44, 14, 20, 13, 9, 9, 18, 6, 9, 11, 42])


def _sheet_sitemap(ws: Worksheet, results: list[PageResult]) -> None:
    header = ["URL (запрошенный)", "Итоговый URL", "HTTP", "Редирект", "Примечание"]
    rows = []
    for page in results:
        requested = page.requested_url or page.url
        final = page.url if (page.url and page.url != requested) else "—"
        redirected = bool(page.url and requested and page.url != requested)
        note = page.check_error or "" if page.check_failed else ""
        rows.append([
            _or_dash(requested), _or_dash(final), _http_label(page),
            _bool_label(redirected), note,
        ])
    _write_rows(ws, header, rows, [46, 46, 7, 10, 48])


# --- «Проблемы» (flat issue list) --------------------------------------------

def _issue_rows(job: Job, content_pages: list[PageResult], results: list[PageResult]) -> list[list]:
    rows: list[list] = []

    def add(url: str, module: str, errors: list[str], warnings: list[str]) -> None:
        for message in errors:
            rows.append([_or_dash(url), module, "Ошибка", message])
        for message in warnings:
            rows.append([_or_dash(url), module, "Предупреждение", message])

    for page in content_pages:
        if page.check_failed:
            continue
        og_errors, og_warnings = _og_issues(page)
        add(page.url, "Open Graph", og_errors, og_warnings)
        add(page.url, "Meta", page.meta.errors, page.meta.warnings)
        add(page.url, "Canonical", page.canonical.errors, page.canonical.warnings)
        add(page.url, "Schema", page.schema_data.errors, page.schema_data.warnings)
        add(page.url, "Images", page.images.errors, page.images.warnings)

    # Availability problems (Sitemap view): unreachable pages and HTTP errors.
    for page in results:
        requested = page.requested_url or page.url
        if page.check_failed:
            rows.append([_or_dash(requested), "Доступность", "Не проверено",
                         page.check_error or "Страницу не удалось проверить"])
        elif page.status_code is not None and page.status_code >= 400:
            rows.append([_or_dash(requested), "Доступность", "Ошибка",
                         f"HTTP {page.status_code}"])

    # Site-level sitemap discovery problems (declared-but-unfetched sitemaps,
    # parse failures, etc.) collected during discovery - not tied to one page.
    site = job.normalized_url or job.requested_url
    for issue in job.sitemap_issues:
        rows.append([_or_dash(site), "Sitemap", "Предупреждение", issue])

    return rows


# --- module totals for the summary sheet -------------------------------------

def _count(errors_warnings: list[PageResult], get) -> tuple[int, int]:
    errors = warnings = 0
    for page in errors_warnings:
        if page.check_failed:
            continue
        e, w = get(page)
        errors += len(e)
        warnings += len(w)
    return errors, warnings


def _sheet_summary(ws: Worksheet, job: Job, content_pages: list[PageResult],
                   results: list[PageResult], issue_count: int) -> None:
    failed = sum(1 for p in results if p.check_failed)

    og = _count(content_pages, lambda p: (p.errors, p.warnings))
    meta = _count(content_pages, lambda p: (p.meta.errors, p.meta.warnings))
    canonical = _count(content_pages, lambda p: (p.canonical.errors, p.canonical.warnings))
    schema = _count(content_pages, lambda p: (p.schema_data.errors, p.schema_data.warnings))
    images = _count(content_pages, lambda p: (p.images.errors, p.images.warnings))

    rows: list[list[str]] = [
        ["Сайт", _or_dash(job.normalized_url or job.requested_url)],
        ["Дата отчёта", datetime.now(timezone.utc).strftime("%Y-%m-%d")],
        ["Статус", _STATUS_LABELS.get(job.status, job.status)],
        ["Страниц найдено", str(job.discovered_urls)],
        ["Страниц проверено", str(job.checked_urls)],
        ["Не удалось проверить", str(failed)],
        ["", ""],
        ["Open Graph", f"ошибок: {og[0]}, предупреждений: {og[1]}"],
        ["Meta", f"ошибок: {meta[0]}, предупреждений: {meta[1]}"],
        ["Canonical", f"ошибок: {canonical[0]}, предупреждений: {canonical[1]}"],
        ["Schema", f"ошибок: {schema[0]}, предупреждений: {schema[1]}"],
        ["Images", f"ошибок: {images[0]}, предупреждений: {images[1]}"],
        ["Всего строк в «Проблемы»", str(issue_count)],
    ]
    if job.partial_reason:
        rows.append(["", ""])
        rows.append(["Проверка завершена не полностью", job.partial_reason])

    ws.append(["Параметр", "Значение"])
    for row in rows:
        ws.append(row)
    _style_header(ws, 2)
    _autosize(ws, [32, 90])
    for row in ws.iter_rows(min_row=2):
        row[0].font = Font(bold=True)
        row[0].alignment = _TOP
        row[1].alignment = _WRAP


def build_report_workbook(job: Job) -> bytes:
    """Build the .xlsx bytes for one audit job. Pure/synchronous; safe to call
    from the request handler (small, in-memory)."""
    results = list(job.results)
    content_pages = _unique_content_pages(results)
    issue_rows = _issue_rows(job, content_pages, results)

    wb = Workbook()
    summary_ws = wb.active
    summary_ws.title = "Сводка"
    _sheet_summary(summary_ws, job, content_pages, results, len(issue_rows))

    _sheet_open_graph(wb.create_sheet("Open Graph"), content_pages)
    _sheet_meta(wb.create_sheet("Meta"), content_pages)
    _sheet_canonical(wb.create_sheet("Canonical"), content_pages)
    _sheet_schema(wb.create_sheet("Schema"), content_pages)
    _sheet_images(wb.create_sheet("Images"), content_pages)
    _sheet_sitemap(wb.create_sheet("Sitemap"), results)

    problems_ws = wb.create_sheet("Проблемы")
    _write_rows(problems_ws, ["URL", "Модуль", "Тип", "Сообщение"], issue_rows,
                [46, 16, 16, 70])

    # Final safety pass: neutralise any site-controlled string that could be
    # read as a formula (Excel formula-injection protection).
    _harden_workbook(wb)

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()

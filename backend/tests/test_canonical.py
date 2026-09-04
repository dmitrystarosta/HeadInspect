"""Phase-A canonical analysis (per-page, no network).

Covers the agreed rules end-to-end from parsed HTML + response headers:
absence, self, other-page, relative, <base href> + relative, several
identical, several different, HTML + HTTP Link agree/conflict, cross-domain,
query, fragment, default ports, empty href, invalid URL, and noindex via both
<meta robots> and X-Robots-Tag. Severity is asserted against the model's
errors/warnings/notes exactly as the frontend consumes them.
"""
from __future__ import annotations

from app.analyzers.canonical import analyze_canonical
from app.htmlmeta import MetadataParser
from app.urlnorm import canonical_equiv_key, urls_equivalent


def _analyze(html: str, headers=None, requested="https://ex.ru/p", final=None):
    parser = MetadataParser()
    parser.feed(html)
    return analyze_canonical(parser, headers or {}, requested, final or requested)


# --- URL equivalence --------------------------------------------------------

def test_equiv_default_port_collapses():
    assert urls_equivalent("https://ex.ru/p", "https://ex.ru:443/p")
    assert urls_equivalent("http://ex.ru/p", "http://ex.ru:80/p")


def test_equiv_www_not_collapsed():
    assert not urls_equivalent("https://ex.ru/p", "https://www.ex.ru/p")


def test_equiv_path_case_and_slash_are_significant():
    assert not urls_equivalent("https://ex.ru/Page/", "https://ex.ru/page/")
    assert not urls_equivalent("https://ex.ru/foo", "https://ex.ru/foo/")


def test_equiv_fragment_ignored():
    assert urls_equivalent("https://ex.ru/p#a", "https://ex.ru/p")


def test_equiv_scheme_host_case_insensitive():
    assert urls_equivalent("HTTPS://EX.RU/p", "https://ex.ru/p")


def test_equiv_key_none_for_non_http():
    assert canonical_equiv_key("ftp://ex.ru/p") is None
    assert canonical_equiv_key("not a url") is None


# --- Phase-A rules ----------------------------------------------------------

def test_absent_canonical_is_warning():
    data = _analyze("<html><head></head></html>")
    assert data.present is False
    assert data.warnings == ["Canonical не указан"]
    assert data.errors == []


def test_self_referencing_is_ok():
    data = _analyze('<link rel="canonical" href="https://ex.ru/p">')
    assert data.is_self is True
    assert data.errors == [] and data.warnings == []


def test_canonical_to_other_same_site_page_is_info():
    data = _analyze('<link rel="canonical" href="https://ex.ru/other">')
    assert data.is_self is False
    assert data.same_site is True
    assert data.errors == [] and data.warnings == []
    assert any("другую страницу" in n for n in data.notes)


def test_relative_canonical_resolves_and_is_not_warning():
    data = _analyze('<link rel="canonical" href="/p">', final="https://ex.ru/p")
    assert data.is_self is True
    assert data.is_relative is True
    assert data.warnings == []
    assert any("Относительный canonical" in n for n in data.notes)


def test_base_href_affects_relative_resolution():
    data = _analyze(
        '<base href="https://ex.ru/blog/"><link rel="canonical" href="item/">',
        final="https://ex.ru/blog/item/",
    )
    assert data.base_href_used is True
    assert data.resolved_url == "https://ex.ru/blog/item/"
    assert data.is_self is True


def test_base_href_empty_is_ignored():
    data = _analyze(
        '<base href=""><link rel="canonical" href="/p">',
        final="https://ex.ru/p",
    )
    assert data.base_href_used is False
    assert data.is_self is True


def test_several_identical_is_warning():
    data = _analyze(
        '<link rel="canonical" href="https://ex.ru/p">'
        '<link rel="canonical" href="https://ex.ru/p">'
    )
    assert data.count == 1
    assert data.conflict is False
    assert any("продублирован" in w for w in data.warnings)


def test_several_different_is_error():
    data = _analyze(
        '<link rel="canonical" href="https://ex.ru/a">'
        '<link rel="canonical" href="https://ex.ru/b">'
    )
    assert data.count == 2
    assert data.conflict is True
    assert any("несколько разных" in e for e in data.errors)


def test_html_and_header_agree_is_duplication_warning_with_both_sources():
    data = _analyze(
        '<link rel="canonical" href="https://ex.ru/a">',
        headers={"link": '<https://ex.ru/a>; rel="canonical"'},
    )
    assert data.source == "both"
    assert data.count == 1
    assert any("продублирован" in w for w in data.warnings)
    assert any("HTML" in n and "Link" in n for n in data.notes)


def test_html_and_header_conflict_is_error():
    data = _analyze(
        '<link rel="canonical" href="https://ex.ru/a">',
        headers={"link": '<https://ex.ru/b>; rel="canonical"'},
    )
    assert data.source == "both"
    assert data.conflict is True
    assert any("Конфликт canonical" in e for e in data.errors)


def test_header_only_canonical_supported():
    data = _analyze(
        "<html><head></head></html>",
        headers={"link": '<https://ex.ru/p>; rel="canonical"'},
        final="https://ex.ru/p",
    )
    assert data.present is True
    assert data.source == "header"
    assert data.is_self is True


def test_cross_domain_is_info_not_error():
    data = _analyze('<link rel="canonical" href="https://other.com/x">')
    assert data.cross_domain is True
    assert data.errors == [] and data.warnings == []
    assert any("другой домен" in n for n in data.notes)


def test_query_params_are_info_not_warning():
    data = _analyze('<link rel="canonical" href="https://ex.ru/p?utm=1">')
    assert data.has_query is True
    assert data.warnings == []
    assert any("query" in n for n in data.notes)


def test_fragment_is_warning():
    data = _analyze('<link rel="canonical" href="https://ex.ru/p#section">')
    assert data.has_fragment is True
    assert any("фрагмент" in w for w in data.warnings)


def test_scheme_mismatch_is_warning():
    data = _analyze(
        '<link rel="canonical" href="http://ex.ru/p">',
        final="https://ex.ru/p",
    )
    assert data.scheme_mismatch is True
    assert any("схему" in w for w in data.warnings)


def test_www_variant_mismatch_is_info():
    data = _analyze(
        '<link rel="canonical" href="https://www.ex.ru/p">',
        final="https://ex.ru/p",
    )
    assert data.host_variant_mismatch is True
    assert data.errors == [] and data.warnings == []


def test_default_port_canonical_counts_as_self():
    data = _analyze(
        '<link rel="canonical" href="https://ex.ru:443/p">',
        final="https://ex.ru/p",
    )
    assert data.is_self is True


def test_empty_href_is_error():
    data = _analyze('<link rel="canonical" href="">')
    assert data.empty_href is True
    assert any("Пустой href" in e for e in data.errors)


def test_invalid_url_is_error():
    data = _analyze('<link rel="canonical" href="http://">')
    assert any("Некорректный URL" in e for e in data.errors)


def test_noindex_via_meta_robots():
    data = _analyze(
        '<meta name="robots" content="noindex,follow">'
        '<link rel="canonical" href="https://ex.ru/p">'
    )
    assert data.page_noindex is True


def test_noindex_via_x_robots_tag_header():
    data = _analyze(
        '<link rel="canonical" href="https://ex.ru/p">',
        headers={"x-robots-tag": "noindex"},
    )
    assert data.page_noindex is True


def test_link_header_parsing_ignores_non_canonical_rel():
    data = _analyze(
        "<html><head></head></html>",
        headers={"link": '<https://ex.ru/next>; rel="next", <https://ex.ru/p>; rel="canonical"'},
        final="https://ex.ru/p",
    )
    assert data.present is True
    assert data.is_self is True


def test_parser_captures_multiple_canonical_and_base():
    parser = MetadataParser()
    parser.feed(
        '<base href="https://ex.ru/"><base href="https://late.ru/">'
        '<link rel="canonical alternate" href="/a">'
        '<link rel="canonical" href="/b">'
    )
    assert parser.base_href == "https://ex.ru/"  # first base wins
    assert parser.canonical_hrefs == ["/a", "/b"]

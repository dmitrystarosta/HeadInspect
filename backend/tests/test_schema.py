"""Extraction of the *contents* of Schema.org (JSON-LD) objects.

Covers the additive `objects` field on SchemaData: @graph, several blocks,
arrays, nested objects, sameAs, BreadcrumbList, a broken block next to a valid
one, the size/depth limits, an XSS string carried through verbatim (escaping is
the frontend's job, but the backend must not mangle or drop it), the no-Schema
case, and - critically - that none of the pre-existing counters change.
"""
from __future__ import annotations

import json

from app.analyzers.schema import analyze_schema
from app.config import (
    SCHEMA_MAX_ARRAY_ITEMS,
    SCHEMA_MAX_OBJECTS_PER_PAGE,
    SCHEMA_MAX_PROPS_PER_OBJECT,
    SCHEMA_MAX_VALUE_CHARS,
)


def _block(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _prop(obj, key):
    for p in obj.properties:
        if p.key == key:
            return p
    raise AssertionError(f"property {key!r} not found in {obj.type}")


def test_simple_object_is_extracted():
    data = analyze_schema(
        [_block({"@context": "https://schema.org", "@type": "WebSite",
                 "name": "GoGoDance", "url": "https://gogodance.ru/"})],
        [],
    )
    assert len(data.objects) == 1
    obj = data.objects[0]
    assert obj.type == "WebSite"
    assert _prop(obj, "name").value == "GoGoDance"
    assert _prop(obj, "url").value == "https://gogodance.ru/"
    # Structural keys are never shown as content rows.
    assert all(p.key not in {"@context", "@type", "@graph"} for p in obj.properties)


def test_multiple_types_on_one_node_join():
    data = analyze_schema(
        [_block({"@context": "https://schema.org",
                 "@type": ["LocalBusiness", "Restaurant"], "name": "X"})],
        [],
    )
    assert data.objects[0].type == "LocalBusiness, Restaurant"


def test_graph_yields_each_entity_separately():
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "name": "GoGoDance"},
            {"@type": "Person", "name": "Иван"},
        ],
    }
    data = analyze_schema([_block(graph)], [])
    # The @graph wrapper (no @type) is NOT promoted to an object.
    assert [o.type for o in data.objects] == ["WebSite", "Person"]


def test_multiple_script_blocks_each_extracted():
    data = analyze_schema(
        [
            _block({"@context": "https://schema.org", "@type": "WebSite", "name": "A"}),
            _block({"@context": "https://schema.org", "@type": "Organization", "name": "B"}),
        ],
        [],
    )
    assert [o.type for o in data.objects] == ["WebSite", "Organization"]


def test_top_level_array_of_objects():
    arr = [
        {"@context": "https://schema.org", "@type": "A", "name": "a"},
        {"@type": "B", "name": "b"},
    ]
    data = analyze_schema([_block(arr)], [])
    assert [o.type for o in data.objects] == ["A", "B"]


def test_nested_object_stays_inside_parent():
    article = {
        "@context": "https://schema.org",
        "@type": "Article",
        "name": "Post",
        "author": {"@type": "Person", "name": "Nested", "url": "https://x/"},
    }
    data = analyze_schema([_block(article)], [])
    # Only the Article is a top-level object; the Person is nested, not promoted.
    assert [o.type for o in data.objects] == ["Article"]
    author = _prop(data.objects[0], "author").value
    assert isinstance(author, dict)
    assert author["type"] == "Person"
    assert {p["key"]: p["value"] for p in author["properties"]}["name"] == "Nested"


def test_same_as_array_preserved_as_list():
    node = {"@context": "https://schema.org", "@type": "Person",
            "name": "Иван", "sameAs": ["https://vk.com/x", "https://t.me/y"]}
    data = analyze_schema([_block(node)], [])
    same_as = _prop(data.objects[0], "sameAs").value
    assert same_as == ["https://vk.com/x", "https://t.me/y"]


def test_breadcrumb_list_items_are_nested_objects():
    crumbs = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Главная", "item": "https://x/"},
            {"@type": "ListItem", "position": 2, "name": "Раздел", "item": "https://x/s"},
        ],
    }
    data = analyze_schema([_block(crumbs)], [])
    obj = data.objects[0]
    assert obj.type == "BreadcrumbList"
    items = _prop(obj, "itemListElement").value
    assert isinstance(items, list) and len(items) == 2
    assert items[0]["type"] == "ListItem"
    names = [{p["key"]: p["value"] for p in it["properties"]}["name"] for it in items]
    assert names == ["Главная", "Раздел"]


def test_broken_block_does_not_stop_valid_one():
    data = analyze_schema(
        [
            "{ this is not valid json",
            _block({"@context": "https://schema.org", "@type": "WebSite", "name": "OK"}),
        ],
        [],
    )
    assert data.invalid_json_ld_count == 1
    assert data.valid_json_ld_count == 1
    # The valid block's object is still extracted.
    assert [o.type for o in data.objects] == ["WebSite"]


def test_long_value_is_clipped_and_flagged():
    long = "a" * (SCHEMA_MAX_VALUE_CHARS + 500)
    node = {"@context": "https://schema.org", "@type": "Thing", "description": long}
    data = analyze_schema([_block(node)], [])
    desc = _prop(data.objects[0], "description")
    assert len(desc.value) == SCHEMA_MAX_VALUE_CHARS
    assert desc.truncated is True


def test_normal_length_value_is_not_clipped():
    # A realistic 400-char description must survive intact (regression against
    # a too-small per-value limit).
    text = "Слово " * 66  # ~ 400 chars
    node = {"@context": "https://schema.org", "@type": "Thing", "description": text}
    data = analyze_schema([_block(node)], [])
    desc = _prop(data.objects[0], "description")
    assert desc.value == text
    assert desc.truncated is False


def test_too_many_objects_are_capped():
    graph = {"@context": "https://schema.org",
             "@graph": [{"@type": "Thing", "name": f"n{i}"} for i in range(SCHEMA_MAX_OBJECTS_PER_PAGE + 10)]}
    data = analyze_schema([_block(graph)], [])
    assert len(data.objects) == SCHEMA_MAX_OBJECTS_PER_PAGE
    assert data.objects_truncated is True


def test_too_many_props_are_capped():
    node = {"@context": "https://schema.org", "@type": "Thing"}
    for i in range(SCHEMA_MAX_PROPS_PER_OBJECT + 10):
        node[f"p{i}"] = f"v{i}"
    data = analyze_schema([_block(node)], [])
    obj = data.objects[0]
    assert len(obj.properties) == SCHEMA_MAX_PROPS_PER_OBJECT
    assert obj.truncated is True


def test_long_array_is_capped():
    node = {"@context": "https://schema.org", "@type": "Person",
            "sameAs": [f"https://x/{i}" for i in range(SCHEMA_MAX_ARRAY_ITEMS + 10)]}
    data = analyze_schema([_block(node)], [])
    same_as = _prop(data.objects[0], "sameAs")
    assert len(same_as.value) == SCHEMA_MAX_ARRAY_ITEMS
    assert same_as.truncated is True


def test_deep_nesting_is_capped_with_placeholder():
    # Build nesting deeper than SCHEMA_MAX_DEPTH; the too-deep object collapses
    # to a compact {type, properties: []} placeholder rather than recursing
    # without bound.
    node = {"@context": "https://schema.org", "@type": "L0",
            "child": {"@type": "L1", "child": {"@type": "L2",
                      "child": {"@type": "L3", "child": {"@type": "L4", "name": "deep"}}}}}
    data = analyze_schema([_block(node)], [])
    obj = data.objects[0]
    assert obj.truncated is True  # some data was omitted somewhere in the tree

    # Walk down and confirm the deepest object was collapsed (empty properties).
    def child(o):
        return {p["key"]: p["value"] for p in o["properties"]}.get("child")

    l1 = _prop(obj, "child").value
    l2 = child(l1)
    l3 = child(l2)
    assert l3["type"] == "L3"
    # L4 is beyond the depth budget -> collapsed placeholder.
    l4 = child(l3)
    assert l4["properties"] == []


def test_xss_string_is_carried_verbatim():
    # The backend must not sanitize/strip; it stores the raw value and the
    # frontend escapes at render time (covered by frontend tests). Here we only
    # assert the value is neither dropped nor mangled.
    payload = "<script>alert('xss')</script>"
    node = {"@context": "https://schema.org", "@type": "Thing", "name": payload}
    data = analyze_schema([_block(node)], [])
    assert _prop(data.objects[0], "name").value == payload


def test_no_schema_means_no_objects():
    data = analyze_schema([], [])
    assert data.objects == []
    assert data.objects_truncated is False


def test_node_without_type_is_not_an_object():
    # A JSON-LD block whose node has no @type still counts as a node for the
    # summary, but has nothing displayable.
    data = analyze_schema([_block({"@context": "https://schema.org", "name": "x"})], [])
    assert data.objects == []
    assert data.node_count == 1  # counter unchanged


def test_existing_counters_are_unchanged_by_object_extraction():
    # Golden values computed from the pre-change counting logic - extraction of
    # `objects` must not perturb any of them.
    graph = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "name": "A"},
        {"@type": "Person", "name": "B", "sameAs": ["https://x/"]},
    ]}
    blocks = [
        _block(graph),
        _block({"@context": "https://schema.org", "@type": "Organization", "name": "C"}),
        "not json at all",
    ]
    data = analyze_schema(blocks, ["https://schema.org/Product"])

    assert data.json_ld_count == 3
    assert data.valid_json_ld_count == 2
    assert data.invalid_json_ld_count == 1
    # @graph wrapper (1) + WebSite + Person + Organization = 4 nodes.
    assert data.node_count == 4
    assert data.types == ["WebSite", "Person", "Organization"]
    assert data.microdata_count == 1
    assert data.microdata_types == ["https://schema.org/Product"]

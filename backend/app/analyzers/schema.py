from __future__ import annotations

import json
from typing import Any

from ..config import (
    SCHEMA_MAX_ARRAY_ITEMS,
    SCHEMA_MAX_CHARS_PER_PAGE,
    SCHEMA_MAX_DEPTH,
    SCHEMA_MAX_OBJECTS_PER_PAGE,
    SCHEMA_MAX_PROPS_PER_OBJECT,
    SCHEMA_MAX_VALUE_CHARS,
)
from ..models import SchemaData, SchemaObject, SchemaProperty


# Keys that describe JSON-LD *structure*, not the entity's own content. They
# are handled elsewhere (@type becomes the object's heading, @graph members
# are surfaced as their own objects) and must not be shown as ordinary
# "field -> value" rows. Everything else, including @id, is kept as content.
_CONTROL_KEYS = frozenset({"@context", "@type", "@graph"})


class _Budget:
    """A shared per-page character budget for normalized Schema data. Once the
    budget is exhausted, extraction stops adding more and callers flag the
    result as truncated - so a single page can never store or return an
    unbounded amount of structured data (protects memory/response size for a
    500-page audit)."""

    __slots__ = ("remaining", "hit")

    def __init__(self, limit: int) -> None:
        self.remaining = limit
        self.hit = False

    def spend(self, text: str) -> bool:
        self.remaining -= len(text)
        if self.remaining < 0:
            self.hit = True
        return not self.hit


def _walk_nodes(value: Any):
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _walk_nodes(item)
        elif isinstance(graph, dict):
            yield from _walk_nodes(graph)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_nodes(item)


def _types_from_node(node: dict[str, Any]) -> list[str]:
    value = node.get("@type")
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_value(value: Any, depth: int, budget: _Budget) -> tuple[Any, bool]:
    """Turn one raw JSON-LD value into a bounded, JSON-safe value for display.
    Returns (normalized_value, truncated). Scalars pass through; long strings
    are clipped; long arrays are cut; nested objects are recursed into as
    {"type", "properties"} dicts until SCHEMA_MAX_DEPTH, then collapsed to a
    compact placeholder. `budget` caps the total across the whole page."""
    if value is None or isinstance(value, bool):
        budget.spend(str(value))
        return value, False

    if isinstance(value, (int, float)):
        budget.spend(str(value))
        return value, False

    if isinstance(value, str):
        truncated = False
        if len(value) > SCHEMA_MAX_VALUE_CHARS:
            value = value[:SCHEMA_MAX_VALUE_CHARS]
            truncated = True
        budget.spend(value)
        return value, truncated

    if isinstance(value, list):
        out: list[Any] = []
        truncated = False
        for index, item in enumerate(value):
            if index >= SCHEMA_MAX_ARRAY_ITEMS or budget.hit:
                truncated = True
                break
            # Arrays are transparent for depth: a list of objects nests no
            # deeper than a single object of the same kind would.
            normalized, item_truncated = _normalize_value(item, depth, budget)
            out.append(normalized)
            truncated = truncated or item_truncated
        return out, truncated

    if isinstance(value, dict):
        if depth + 1 > SCHEMA_MAX_DEPTH:
            # Too deep to expand further - keep just the type as a marker.
            return {"type": ", ".join(_types_from_node(value)), "properties": []}, True
        props, props_truncated = _normalize_props(value, depth + 1, budget)
        return {"type": ", ".join(_types_from_node(value)), "properties": props}, props_truncated

    # Any other JSON scalar type (shouldn't occur) - stringify defensively.
    text = str(value)
    if len(text) > SCHEMA_MAX_VALUE_CHARS:
        text = text[:SCHEMA_MAX_VALUE_CHARS]
    budget.spend(text)
    return text, False


def _normalize_props(node: dict[str, Any], depth: int, budget: _Budget) -> tuple[list[dict[str, Any]], bool]:
    """Normalize a node's own content properties (skipping structural keys)
    into an ordered list of {"key", "value", "truncated"} dicts, bounded by
    the per-object property cap and the shared page budget."""
    props: list[dict[str, Any]] = []
    truncated = False
    count = 0
    for key, raw in node.items():
        if key in _CONTROL_KEYS:
            continue
        if count >= SCHEMA_MAX_PROPS_PER_OBJECT or budget.hit:
            truncated = True
            break
        budget.spend(str(key))
        value, value_truncated = _normalize_value(raw, depth, budget)
        props.append({"key": str(key), "value": value, "truncated": value_truncated})
        truncated = truncated or value_truncated
        count += 1
    return props, truncated


def analyze_schema(json_ld_blocks: list[str], microdata_types: list[str]) -> SchemaData:
    errors: list[str] = []
    warnings: list[str] = []
    types: list[str] = []
    valid_blocks = 0
    invalid_blocks = 0
    node_count = 0

    # Human-readable object extraction (additive - does not affect any counter
    # above). Shared across all JSON-LD blocks of the page so the size limits
    # apply per page, not per block.
    objects: list[SchemaObject] = []
    objects_truncated = False
    budget = _Budget(SCHEMA_MAX_CHARS_PER_PAGE)

    for index, raw in enumerate(json_ld_blocks, start=1):
        text = raw.strip()
        if not text:
            invalid_blocks += 1
            errors.append(f"JSON-LD блок {index}: пустой script")
            continue

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            invalid_blocks += 1
            errors.append(
                f"JSON-LD блок {index}: ошибка JSON, строка {exc.lineno}, столбец {exc.colno}"
            )
            continue

        valid_blocks += 1
        nodes = list(_walk_nodes(payload))
        node_count += len(nodes)
        if not nodes:
            warnings.append(f"JSON-LD блок {index}: не найден объект Schema.org")
            continue

        block_has_type = False
        block_has_schema_context = False
        for node in nodes:
            context = node.get("@context")
            if isinstance(context, str) and "schema.org" in context.lower():
                block_has_schema_context = True
            elif isinstance(context, list) and any(
                isinstance(item, str) and "schema.org" in item.lower() for item in context
            ):
                block_has_schema_context = True

            node_types = _types_from_node(node)
            if node_types:
                block_has_type = True
                types.extend(node_types)

                # Surface the node's actual content as a displayable object.
                # Only real entities (with an @type) become top-level objects;
                # the @graph wrapper and nested technical structures are never
                # promoted here. Nothing in this block touches the counters.
                if len(objects) >= SCHEMA_MAX_OBJECTS_PER_PAGE or budget.hit:
                    objects_truncated = True
                else:
                    props, props_truncated = _normalize_props(node, 1, budget)
                    objects.append(
                        SchemaObject(
                            type=", ".join(node_types),
                            properties=[SchemaProperty(**prop) for prop in props],
                            truncated=props_truncated or budget.hit,
                        )
                    )
                    if budget.hit:
                        objects_truncated = True

        # @context is commonly declared once on the root object and inherited by @graph nodes.
        root_context = payload.get("@context") if isinstance(payload, dict) else None
        if isinstance(root_context, str) and "schema.org" in root_context.lower():
            block_has_schema_context = True

        if not block_has_schema_context:
            warnings.append(f"JSON-LD блок {index}: не найден @context Schema.org")
        if not block_has_type:
            warnings.append(f"JSON-LD блок {index}: не найден @type")

    unique_types = list(dict.fromkeys(types))
    unique_microdata = list(dict.fromkeys(microdata_types))

    if not json_ld_blocks and not microdata_types:
        warnings.append("Структурированные данные Schema.org не найдены")
    elif not json_ld_blocks and microdata_types:
        warnings.append("Найдена Microdata, но JSON-LD не найден")

    if len(json_ld_blocks) > 10:
        warnings.append(f"На странице много JSON-LD блоков: {len(json_ld_blocks)}")

    return SchemaData(
        json_ld_count=len(json_ld_blocks),
        valid_json_ld_count=valid_blocks,
        invalid_json_ld_count=invalid_blocks,
        node_count=node_count,
        types=unique_types,
        microdata_count=len(microdata_types),
        microdata_types=unique_microdata,
        objects=objects,
        objects_truncated=objects_truncated,
        errors=errors,
        warnings=warnings,
    )

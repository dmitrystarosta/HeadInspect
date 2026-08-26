from __future__ import annotations

import json
from typing import Any

from ..models import SchemaData


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


def analyze_schema(json_ld_blocks: list[str], microdata_types: list[str]) -> SchemaData:
    errors: list[str] = []
    warnings: list[str] = []
    types: list[str] = []
    valid_blocks = 0
    invalid_blocks = 0
    node_count = 0

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
        errors=errors,
        warnings=warnings,
    )

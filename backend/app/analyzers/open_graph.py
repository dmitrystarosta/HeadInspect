from __future__ import annotations

from io import BytesIO

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

from ..config import (
    MAX_IMAGE_PIXELS,
    MAX_OG_IMAGE_BYTES,
    MIN_OG_HEIGHT,
    MIN_OG_WIDTH,
    RECOMMENDED_OG_HEIGHT,
    RECOMMENDED_OG_WIDTH,
    WARN_OG_IMAGE_BYTES,
)
from ..fetcher import safe_fetch
from ..models import OpenGraphData
from ..security import resolve_relative_url, validate_public_url

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def _first(og: dict[str, list[str]], key: str) -> str | None:
    values = og.get(key, [])
    return values[0] if values else None


async def analyze_open_graph(
    og: dict[str, list[str]],
    page_url: str,
) -> tuple[OpenGraphData, list[str], list[str]]:
    images = og.get("og:image", []) + og.get("og:image:url", [])
    unique_images = list(dict.fromkeys(images))

    image_raw = unique_images[0] if unique_images else _first(og, "og:image:secure_url")

    data = OpenGraphData(
        title=_first(og, "og:title"),
        description=_first(og, "og:description"),
        url=_first(og, "og:url"),
        type=_first(og, "og:type"),
        image=image_raw,
        image_count=len(unique_images),
        image_width_declared=_first(og, "og:image:width"),
        image_height_declared=_first(og, "og:image:height"),
    )

    errors: list[str] = []
    warnings: list[str] = []

    if not data.title:
        errors.append("Нет og:title")
    if not data.image:
        errors.append("Нет og:image")
    if not data.description:
        warnings.append("Нет og:description")
    if not data.url:
        warnings.append("Нет og:url")
    if not data.type:
        warnings.append("Нет og:type")
    if data.image_count > 1:
        warnings.append(f"Найдено несколько og:image: {data.image_count}")

    if not data.image:
        return data, errors, warnings

    try:
        absolute_image_url = resolve_relative_url(page_url, data.image)
        absolute_image_url = await validate_public_url(absolute_image_url)
        data.image = absolute_image_url
    except HTTPException as exc:
        data.image_accessible = False
        errors.append(f"Некорректный og:image: {exc.detail}")
        return data, errors, warnings

    image_headers = {
        # A standards-compliant, honest crawler UA in the conventional Mozilla-compatible form.
        # Some CDNs return an empty 204 response to very minimal/unknown bot requests.
        "User-Agent": "Mozilla/5.0 (compatible; HeadInspectBot/0.3; +https://headinspect.ru/)",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": page_url,
    }

    try:
        image_result = await safe_fetch(
            data.image,
            max_bytes=MAX_OG_IMAGE_BYTES,
            accepted_content_types=("image/", "application/octet-stream"),
            request_headers=image_headers,
        )
    except HTTPException as exc:
        data.image_accessible = False
        errors.append(f"og:image недоступен: {exc.detail}")
        return data, errors, warnings

    data.image_status_code = image_result.status_code
    data.image_content_type = image_result.headers.get("content-type", "").split(";", 1)[0].strip().lower() or None
    data.image_bytes = len(image_result.content)

    if image_result.status_code >= 400:
        data.image_accessible = False
        errors.append(f"og:image возвращает HTTP {image_result.status_code}")
        return data, errors, warnings

    # A 2xx response is not enough for image analysis: e.g. some CDNs answer
    # with 204 No Content. Treat an empty body as an inaccessible image for
    # this crawler instead of passing zero bytes to Pillow and reporting a
    # misleading "format/size" error.
    if not image_result.content:
        data.image_accessible = False
        errors.append(
            f"og:image вернул пустой ответ HTTP {image_result.status_code}; "
            "изображение не удалось получить для анализа"
        )
        return data, errors, warnings

    data.image_accessible = True

    if data.image_content_type and not data.image_content_type.startswith("image/"):
        errors.append(f"og:image имеет неожиданный Content-Type: {data.image_content_type}")

    try:
        with Image.open(BytesIO(image_result.content)) as image:
            data.image_format = (image.format or "").upper() or None
            data.image_width, data.image_height = image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        errors.append("Не удалось определить формат или размеры og:image")
        return data, errors, warnings

    if data.image_bytes is not None and data.image_bytes > WARN_OG_IMAGE_BYTES:
        warnings.append(f"Тяжёлое og:image: {data.image_bytes / 1024 / 1024:.1f} MB")

    if data.image_width is not None and data.image_height is not None:
        if data.image_width < MIN_OG_WIDTH or data.image_height < MIN_OG_HEIGHT:
            warnings.append(
                f"Маленькое og:image: {data.image_width}×{data.image_height}"
            )
        elif (data.image_width, data.image_height) != (RECOMMENDED_OG_WIDTH, RECOMMENDED_OG_HEIGHT):
            warnings.append(
                f"Нестандартный размер og:image: {data.image_width}×{data.image_height}"
            )

    try:
        declared_width = int(data.image_width_declared) if data.image_width_declared else None
    except ValueError:
        declared_width = None
        warnings.append("Некорректный og:image:width")

    try:
        declared_height = int(data.image_height_declared) if data.image_height_declared else None
    except ValueError:
        declared_height = None
        warnings.append("Некорректный og:image:height")

    if declared_width is not None and data.image_width is not None and declared_width != data.image_width:
        warnings.append(
            f"og:image:width={declared_width}, фактически {data.image_width}"
        )

    if declared_height is not None and data.image_height is not None and declared_height != data.image_height:
        warnings.append(
            f"og:image:height={declared_height}, фактически {data.image_height}"
        )

    return data, errors, warnings

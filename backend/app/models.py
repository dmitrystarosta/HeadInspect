from pydantic import BaseModel, Field

class AuditRequest(BaseModel):
    url: str = Field(..., examples=["https://example.ru"])

class OpenGraphData(BaseModel):
    title: str | None = None
    description: str | None = None
    url: str | None = None
    type: str | None = None
    image: str | None = None
    image_width: str | None = None
    image_height: str | None = None
    image_count: int = 0

class PageResult(BaseModel):
    url: str
    status_code: int | None = None
    title: str | None = None
    meta_description: str | None = None
    open_graph: OpenGraphData
    errors: list[str] = []
    warnings: list[str] = []

class AuditResponse(BaseModel):
    requested_url: str
    normalized_url: str
    robots_url: str
    robots_found: bool
    sitemap_urls: list[str]
    discovered_urls: int
    checked_urls: int
    limited: bool
    results: list[PageResult]

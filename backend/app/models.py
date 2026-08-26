from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AuditRequest(BaseModel):
    url: str = Field(..., examples=["https://example.ru"])


class OpenGraphData(BaseModel):
    title: str | None = None
    description: str | None = None
    url: str | None = None
    type: str | None = None

    image: str | None = None
    image_count: int = 0
    image_width_declared: str | None = None
    image_height_declared: str | None = None

    image_accessible: bool | None = None
    image_status_code: int | None = None
    image_content_type: str | None = None
    image_format: str | None = None
    image_bytes: int | None = None
    image_width: int | None = None
    image_height: int | None = None


class MetaData(BaseModel):
    title: str | None = None
    title_count: int = 0
    description: str | None = None
    description_count: int = 0
    keywords: str | None = None
    keywords_count: int = 0
    robots: str | None = None
    robots_count: int = 0
    viewport: str | None = None
    viewport_count: int = 0
    lang: str | None = None
    charset: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SchemaData(BaseModel):
    json_ld_count: int = 0
    valid_json_ld_count: int = 0
    invalid_json_ld_count: int = 0
    node_count: int = 0
    types: list[str] = Field(default_factory=list)
    microdata_count: int = 0
    microdata_types: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PageResult(BaseModel):
    url: str
    status_code: int | None = None
    title: str | None = None
    meta_description: str | None = None
    open_graph: OpenGraphData = Field(default_factory=OpenGraphData)
    meta: MetaData = Field(default_factory=MetaData)
    schema_data: SchemaData = Field(Field(default_factory=SchemaData), alias="schema")
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


JobStatus = Literal["queued", "discovering", "running", "completed", "failed"]


class AuditCreateResponse(BaseModel):
    job_id: str
    status: JobStatus
    status_url: str
    results_url: str


class AuditJobStatus(BaseModel):
    job_id: str
    status: JobStatus
    requested_url: str
    normalized_url: str | None = None
    robots_url: str | None = None
    robots_found: bool | None = None
    sitemap_urls: list[str] = Field(default_factory=list)
    discovered_urls: int = 0
    checked_urls: int = 0
    max_urls: int = 500
    limited: bool = False
    progress_percent: int = 0
    errors_found: int = 0
    warnings_found: int = 0
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class AuditResultsResponse(BaseModel):
    job_id: str
    status: JobStatus
    checked_urls: int
    discovered_urls: int
    results: list[PageResult] = Field(default_factory=list)

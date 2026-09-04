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


class CanonicalData(BaseModel):
    # --- Phase A: from this page's own HTML + response headers, no network.
    present: bool = False
    count: int = 0                      # distinct canonical *targets* found
    html_count: int = 0                 # <link rel="canonical"> tags seen
    header_count: int = 0               # Link: rel="canonical" values seen
    raw_href: str | None = None         # first raw href, as written
    raw_hrefs: list[str] = Field(default_factory=list)
    resolved_url: str | None = None     # chosen canonical, absolute
    source: Literal["none", "html", "header", "both"] = "none"
    is_relative: bool = False
    empty_href: bool = False
    valid_url: bool = False
    is_self: bool | None = None
    same_site: bool | None = None
    cross_domain: bool | None = None
    scheme_mismatch: bool | None = None
    host_variant_mismatch: bool | None = None  # www vs non-www vs the page
    has_fragment: bool = False
    has_query: bool = False
    base_href_used: bool = False        # a <base href> affected resolution
    conflict: bool = False              # several *different* signals
    # This page's own indexability, from <meta name="robots"> AND the
    # X-Robots-Tag response header - stored per page so the resolution pass
    # can read a *target* page's noindex without any extra request.
    page_noindex: bool = False

    # --- Phase B: filled by the post-audit resolution pass, purely from the
    # already-collected results map. None means "not applicable / not
    # resolvable from what this audit actually fetched".
    target_in_audit: bool | None = None
    target_status: int | None = None
    target_redirected: bool | None = None
    target_final_url: str | None = None
    target_noindex: bool | None = None
    target_canonical: str | None = None
    chain: list[str] = Field(default_factory=list)
    cycle: bool = False

    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


CheckFailureReason = Literal["network", "timeout", "content_type", "access_blocked"]


class PageResult(BaseModel):
    url: str
    requested_url: str | None = None
    status_code: int | None = None
    check_failed: bool = False
    check_error: str | None = None
    # Structured reason a check_failed page could not be reliably checked -
    # lets the frontend branch on a stable value instead of pattern-matching
    # check_error's free text (which is still shown to the user as-is, but
    # is no longer the only way to know *why*):
    #   "network"        - DNS/connection failure, no HTTP response at all
    #   "timeout"         - no HTTP response within the time budget
    #   "content_type"    - a response was received but wasn't analyzable
    #                       HTML (wrong content type, or too large)
    #   "access_blocked"  - a response WAS received (status_code is set),
    #                       but it was 401/403/429 - likely a WAF/verification
    #                       page rather than the site's real content, so
    #                       Open Graph/Meta/Schema analysis was skipped
    # None when check_failed is False.
    check_reason: CheckFailureReason | None = None
    title: str | None = None
    meta_description: str | None = None
    open_graph: OpenGraphData = Field(default_factory=OpenGraphData)
    meta: MetaData = Field(default_factory=MetaData)
    schema_data: SchemaData = Field(default_factory=SchemaData, serialization_alias="schema")
    canonical: CanonicalData = Field(default_factory=CanonicalData)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


JobStatus = Literal["queued", "discovering", "running", "completed", "completed_partial", "failed"]


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
    robots_sitemap_urls: list[str] = Field(default_factory=list)
    sitemap_urls: list[str] = Field(default_factory=list)
    sitemap_issues: list[str] = Field(default_factory=list)
    discovered_urls: int = 0
    checked_urls: int = 0
    max_urls: int = 500
    limited: bool = False
    progress_percent: int = 0
    errors_found: int = 0
    warnings_found: int = 0
    failed_checks: int = 0
    access_blocked_status: int | None = None
    # Set when the site appears to have started blocking HeadInspect's
    # automated requests partway through the audit (see JobManager's
    # mid-audit block detection), as opposed to a single page legitimately
    # returning 401/403/429 on its own.
    blocked_mid_audit: bool = False
    mid_audit_block_status: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    # Populated only when status == "completed_partial": a human-readable,
    # non-alarming explanation of why the audit is incomplete (AUDIT_TIMEOUT
    # reached, or the site started blocking automated requests), distinct
    # from `error`, which is reserved for status == "failed".
    partial_reason: str | None = None


class AuditResultsResponse(BaseModel):
    job_id: str
    status: JobStatus
    checked_urls: int
    discovered_urls: int
    failed_checks: int = 0
    results: list[PageResult] = Field(default_factory=list)

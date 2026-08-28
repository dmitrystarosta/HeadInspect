from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import APP_NAME, APP_VERSION
from .jobs import job_manager
from .models import AuditCreateResponse, AuditJobStatus, AuditRequest, AuditResultsResponse

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://headinspect.ru",
        "https://www.headinspect.ru",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "headinspect-api",
        "version": APP_VERSION,
    }


@app.post("/api/audits", response_model=AuditCreateResponse, status_code=202)
async def create_audit(payload: AuditRequest, request: Request) -> AuditCreateResponse:
    # The API is bound to localhost and is reached through our nginx reverse proxy,
    # which sets X-Real-IP. Fall back to the socket peer for local/direct requests.
    client_ip = request.headers.get("x-real-ip")
    if not client_ip:
        client_ip = request.client.host if request.client else "unknown"

    job = await job_manager.create(payload.url, client_ip=client_ip)
    return AuditCreateResponse(
        job_id=job.job_id,
        status=job.status,
        status_url=f"/api/audits/{job.job_id}",
        results_url=f"/api/audits/{job.job_id}/results",
    )


@app.get("/api/audits/{job_id}", response_model=AuditJobStatus)
async def audit_status(job_id: str) -> AuditJobStatus:
    job = job_manager.get(job_id)
    return job_manager.status_model(job)


@app.get("/api/audits/{job_id}/results", response_model=AuditResultsResponse)
async def audit_results(job_id: str) -> AuditResultsResponse:
    job = job_manager.get(job_id)
    return job_manager.results_model(job)

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from . import stats
from .config import APP_NAME, APP_VERSION
from .jobs import job_manager
from .models import AuditCreateResponse, AuditJobStatus, AuditRequest, AuditResultsResponse
from .report import build_report_workbook, report_filename


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialise the (optional) stats DB and reconcile any runs left in a
    # non-terminal state by a previous process, so a Docker restart can never
    # leave audits stuck as "running" in the daily report. No-op unless
    # HEADINSPECT_STATS_DB is set; failures never block startup.
    try:
        stats.init_db()
        reconciled = stats.reconcile_running()
        if reconciled:
            import logging
            logging.getLogger("uvicorn.error").info(
                "Startup: reconciled %s interrupted audit run(s) in stats", reconciled
            )
    except Exception:
        import logging
        logging.getLogger("uvicorn.error").exception("Startup stats reconcile failed")
    yield


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
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
    # Let the browser read the download filename on the cross-origin
    # report.xlsx response (frontend fetches it as a blob).
    expose_headers=["Content-Disposition"],
)

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Statuses for which a report is not yet meaningful - the crawl has not
# produced a result set to export. completed / completed_partial pass.
_UNFINISHED_STATUSES = frozenset({"queued", "discovering", "running"})


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

    job = await job_manager.create(payload.url, client_ip=client_ip, source=payload.source)
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


@app.get("/api/audits/{job_id}/report.xlsx")
async def audit_report(job_id: str) -> Response:
    job = job_manager.get(job_id)
    if job.status in _UNFINISHED_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Проверка ещё не завершена. Дождитесь окончания аудита и повторите скачивание.",
        )

    # Building a 500-page workbook with openpyxl is CPU-bound (~0.3 s), enough
    # to stall the single event loop and every other in-flight audit. Offload
    # it to a worker thread so the API stays responsive; the job is only read,
    # never mutated, so this is safe.
    content = await asyncio.to_thread(build_report_workbook, job)
    filename = report_filename(job)
    # RFC 5987 encoding so a non-ASCII hostname in the filename survives.
    disposition = f"attachment; filename=\"report.xlsx\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": disposition},
    )

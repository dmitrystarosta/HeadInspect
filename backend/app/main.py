from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .audit import run_audit
from .config import APP_NAME, APP_VERSION
from .models import AuditRequest, AuditResponse

app=FastAPI(title=APP_NAME,version=APP_VERSION,docs_url="/docs",redoc_url=None)
app.add_middleware(CORSMiddleware,
    allow_origins=["https://headinspect.ru","https://www.headinspect.ru"],
    allow_credentials=False,allow_methods=["GET","POST","OPTIONS"],allow_headers=["Content-Type"])

@app.get("/health")
async def health():
    return {"status":"ok","service":"headinspect-api","version":APP_VERSION}

@app.post("/api/audit",response_model=AuditResponse)
async def audit(payload: AuditRequest):
    try: return await run_audit(payload.url)
    except HTTPException: raise
    except Exception as exc: raise HTTPException(status_code=500,detail="Audit failed") from exc

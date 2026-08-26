"""FastAPI web front end for the Investor Presentation Review.

    uvicorn web.main:app --reload            # local
    uvicorn web.main:app --host 0.0.0.0 --port $PORT   # Railway

Endpoints
---------
GET  /                          the upload page
GET  /health                    liveness probe
GET  /api/config                what the client needs to know before uploading
POST /api/review                upload a deck, returns a job id
GET  /api/jobs/{id}             poll job status; carries the review when done
GET  /api/jobs/{id}/report.pdf  the one-page PDF
GET  /api/jobs/{id}/report.json the structured analysis
GET  /api/template.pdf          the blank structure template

The API key is read from the environment and is never returned by any endpoint.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # optional locally, unused on Railway
    pass

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from analysis.llm_provider import AnthropicProvider, OpenAIProvider
from utils.validation import SUPPORTED_EXTENSIONS
from web.jobs import DONE, FAILED, store

MAX_UPLOAD_BYTES = 40 * 1024 * 1024
CHUNK = 1024 * 1024

STATIC_DIR = Path(__file__).parent / "static"
# Railway's filesystem is ephemeral, which suits per-job scratch space exactly.
WORK_ROOT = Path(tempfile.gettempdir()) / "deck_review_jobs"
WORK_ROOT.mkdir(parents=True, exist_ok=True)

# Optional shared-secret gate. A public URL that spends your Anthropic credits on
# every upload is a liability, so set ACCESS_CODE in the Railway variables.
ACCESS_CODE = (os.getenv("ACCESS_CODE") or "").strip()

app = FastAPI(
    title="Investor Presentation Review",
    description="Upload a pitch deck, get a one-page investor review.",
    version="1.0.0",
    docs_url="/api/docs",
)


# --------------------------------------------------------------------------- #
# Pages and metadata
# --------------------------------------------------------------------------- #

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    page = STATIC_DIR / "index.html"
    if not page.exists():
        return HTMLResponse("<h1>Investor Presentation Review</h1>"
                            "<p>Front end not found.</p>", status_code=500)
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "provider": _configured_provider(),
        "active_jobs": store.active_count(),
    })


@app.get("/api/config")
def config() -> JSONResponse:
    """Everything the client needs, and nothing sensitive.

    The upload page renders its accepted formats, size cap and email disclosure from
    this payload rather than hard-coding them, so what a user is told always matches
    what the server actually enforces.
    """
    from notifications.settings import EmailSettings

    email = EmailSettings.from_env()
    return JSONResponse({
        "accepted_formats": sorted(SUPPORTED_EXTENSIONS),
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "access_code_required": bool(ACCESS_CODE),
        "provider": _configured_provider(),
        "model": os.getenv("DECK_REVIEW_MODEL") or "claude-opus-5",
        "email_enabled": email.enabled,
        "email_to": ", ".join(email.recipients) if email.enabled else None,
    })


def _configured_provider() -> str:
    if AnthropicProvider.available():
        return "anthropic"
    if OpenAIProvider.available():
        return "openai"
    return "heuristic"


# --------------------------------------------------------------------------- #
# Review lifecycle
# --------------------------------------------------------------------------- #

@app.post("/api/review")
async def create_review(
    request: Request,
    file: UploadFile = File(...),
    access_code: Optional[str] = Form(None),
) -> JSONResponse:
    _check_access(access_code or request.headers.get("x-access-code"))

    filename = Path(file.filename or "deck").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail={
                "error": "Unsupported file type '{}'.".format(suffix or "none"),
                "hint": "Upload a PDF, PPTX or PPT file.",
            },
        )

    workdir = Path(tempfile.mkdtemp(prefix="job_", dir=str(WORK_ROOT)))
    upload_path = workdir / filename

    try:
        written = await _save_upload(file, upload_path)
    except HTTPException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise

    if written == 0:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(
            status_code=400,
            detail={"error": "The uploaded file is empty.",
                    "hint": "Re-export the deck and try again."},
        )

    try:
        job = store.submit(upload_path, filename, workdir)
    except RuntimeError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(status_code=503,
                            detail={"error": str(exc), "hint": None}) from exc

    return JSONResponse(job.public(), status_code=202)


async def _save_upload(file: UploadFile, destination: Path) -> int:
    """Stream to disk, aborting past the size cap rather than buffering it all."""
    written = 0
    with open(destination, "wb") as handle:
        while True:
            chunk = await file.read(CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                handle.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail={
                        "error": "File exceeds the {} MB limit.".format(
                            MAX_UPLOAD_BYTES // (1024 * 1024)),
                        "hint": "Compress the images and re-export the deck.",
                    },
                )
            handle.write(chunk)
    return written


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> JSONResponse:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404,
                            detail={"error": "Unknown or expired job.", "hint": None})
    return JSONResponse(job.public(), status_code=200 if job.status != FAILED else 200)


@app.get("/api/jobs/{job_id}/report.pdf")
def download_pdf(job_id: str) -> FileResponse:
    job = _finished_job(job_id)
    if not job.pdf_path or not job.pdf_path.exists():
        raise HTTPException(status_code=404,
                            detail={"error": "Report file is no longer available.",
                                    "hint": "Run the review again."})
    return FileResponse(job.pdf_path, media_type="application/pdf",
                        filename=job.download_stem + ".pdf")


@app.get("/api/jobs/{job_id}/report.json")
def download_json(job_id: str) -> FileResponse:
    job = _finished_job(job_id)
    if not job.json_path or not job.json_path.exists():
        raise HTTPException(status_code=404,
                            detail={"error": "Analysis file is no longer available.",
                                    "hint": "Run the review again."})
    return FileResponse(job.json_path, media_type="application/json",
                        filename=job.download_stem + ".json")


def _finished_job(job_id: str):
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404,
                            detail={"error": "Unknown or expired job.", "hint": None})
    if job.status != DONE:
        raise HTTPException(status_code=409,
                            detail={"error": "The review is not finished yet.",
                                    "hint": None})
    return job


# --------------------------------------------------------------------------- #
# Structure template
# --------------------------------------------------------------------------- #

@app.get("/api/template.pdf")
def template_pdf() -> FileResponse:
    """The blank report structure template, rendered on demand."""
    from reports.template_document import TEMPLATE_FILENAME, write_template

    out = WORK_ROOT / "template"
    created = write_template(out)
    return FileResponse(created["pdf"], media_type="application/pdf",
                        filename=TEMPLATE_FILENAME + ".pdf")


# --------------------------------------------------------------------------- #
# Access control and error shape
# --------------------------------------------------------------------------- #

def _check_access(supplied: Optional[str]) -> None:
    if not ACCESS_CODE:
        return
    if (supplied or "").strip() != ACCESS_CODE:
        raise HTTPException(
            status_code=401,
            detail={"error": "Incorrect access code.",
                    "hint": "Ask the owner of this deployment for the code."},
        )


@app.exception_handler(HTTPException)
def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    """Always return {error, hint} so the client can render failures uniformly."""
    detail = exc.detail
    if isinstance(detail, dict):
        body = {"error": detail.get("error", "Request failed."),
                "hint": detail.get("hint")}
    else:
        body = {"error": str(detail), "hint": None}
    return JSONResponse(body, status_code=exc.status_code)

"""Background job execution for the web app.

A full review takes one to three minutes: extraction is fast, but the model call at
high effort is not. That is far too long to hold an HTTP request open through
Railway's proxy and a browser, so uploads become jobs that the client polls.

Concurrency is capped deliberately. Each job spends real money on the Anthropic API,
so the worker pool is small and the queue is bounded rather than unbounded.
"""

from __future__ import annotations

import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from analysis.investor_review import review_deck
from analysis.llm_provider import get_provider
from extractors import extract_deck
from models.schemas import slugify
from reports.pdf_generator import generate_pdf
from utils.file_utils import write_json
from utils.validation import DeckReviewError

# Jobs are held in memory, so a restart loses them and a second instance cannot see
# them. That is acceptable for single-instance use; a shared store would be needed
# before scaling horizontally.
JOB_TTL_SECONDS = 2 * 60 * 60
MAX_WORKERS = 2
MAX_QUEUED = 8

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


@dataclass
class Job:
    id: str
    filename: str
    status: str = QUEUED
    stage: str = "Queued"
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None
    hint: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    pdf_path: Optional[Path] = None
    json_path: Optional[Path] = None
    workdir: Optional[Path] = None
    download_stem: str = "investor_presentation_review"
    email: Optional[Dict[str, Any]] = None

    def public(self) -> Dict[str, Any]:
        """The client-visible shape. Never leaks filesystem paths."""
        payload: Dict[str, Any] = {
            "id": self.id,
            "filename": self.filename,
            "status": self.status,
            "stage": self.stage,
            "elapsed": round((self.finished_at or time.time()) - self.created_at, 1),
        }
        if self.status == FAILED:
            payload["error"] = self.error
            payload["hint"] = self.hint
        if self.status == DONE and self.result:
            payload["review"] = self.result
            payload["downloads"] = {
                "pdf": "/api/jobs/{}/report.pdf".format(self.id),
                "json": "/api/jobs/{}/report.json".format(self.id),
            }
            payload["email"] = self.email
        return payload


class JobStore:
    """Thread-safe job registry with a bounded worker pool and TTL cleanup."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=MAX_WORKERS,
                                        thread_name_prefix="review")

    # -- registry --------------------------------------------------------- #

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status in (QUEUED, RUNNING))

    def submit(self, upload_path: Path, filename: str, workdir: Path,
               provider_name: Optional[str] = None) -> Job:
        if self.active_count() >= MAX_QUEUED:
            raise RuntimeError(
                "The server is at capacity ({} reviews in progress). "
                "Try again in a minute.".format(MAX_QUEUED)
            )

        job = Job(id=uuid.uuid4().hex[:16], filename=filename, workdir=workdir)
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job, upload_path, provider_name)
        self._sweep()
        return job

    # -- execution -------------------------------------------------------- #

    def _run(self, job: Job, upload_path: Path, provider_name: Optional[str]) -> None:
        try:
            job.status = RUNNING

            job.stage = "Extracting slides"
            deck = extract_deck(str(upload_path))

            job.stage = "Analyzing {} slides".format(deck.slide_count)
            provider = get_provider(provider_name)
            result = review_deck(deck, provider=provider)

            job.stage = "Generating the one-page report"
            stem = "{}_investor_presentation_review".format(slugify(result.company))
            job.download_stem = stem
            assert job.workdir is not None
            pdf_path = job.workdir / (stem + ".pdf")
            generate_pdf(result, pdf_path)
            json_path = write_json(result.to_dict(), job.workdir / (stem + ".json"))

            job.pdf_path = pdf_path
            job.json_path = json_path

            job.stage = "Emailing the report"
            job.email = _email(result, pdf_path, json_path, job.filename)

            job.result = _summary(result, deck)
            job.stage = "Complete"
            job.status = DONE

        except DeckReviewError as exc:
            job.status = FAILED
            job.stage = "Failed"
            job.error = str(exc)
            job.hint = getattr(exc, "hint", "") or None
        except Exception as exc:  # noqa: BLE001 - a worker must never die silently
            job.status = FAILED
            job.stage = "Failed"
            job.error = "Unexpected error: {}".format(exc)
            job.hint = "Please retry. If it persists, the deck may be unusual."
            traceback.print_exc()
        finally:
            job.finished_at = time.time()
            try:
                upload_path.unlink(missing_ok=True)
            except OSError:
                pass

    # -- housekeeping ------------------------------------------------------ #

    def _sweep(self) -> None:
        """Delete finished jobs and their files once the TTL has passed."""
        cutoff = time.time() - JOB_TTL_SECONDS
        with self._lock:
            expired = [
                job_id for job_id, job in self._jobs.items()
                if job.finished_at and job.finished_at < cutoff
            ]
            for job_id in expired:
                job = self._jobs.pop(job_id)
                if job.workdir and job.workdir.exists():
                    shutil.rmtree(job.workdir, ignore_errors=True)


def _email(result, pdf_path: Path, json_path: Path, filename: str) -> Dict[str, Any]:
    """Send the report email. Never raises -- the review is already complete."""
    try:
        from notifications import send_review_notification

        outcome = send_review_notification(
            result, pdf_path=pdf_path, json_path=json_path,
            source_filename=filename, origin="web upload",
        )
        return outcome.to_dict()
    except Exception as exc:  # noqa: BLE001 - delivery must never fail a finished job
        return {"sent": False, "error": "email step failed: {}".format(exc),
                "summary": "Email failed: {}".format(exc), "recipients": []}


def _summary(result, deck) -> Dict[str, Any]:
    """The review, plus the extraction facts the UI shows alongside it."""
    payload = result.to_dict()
    payload["extraction"] = {
        "file_type": deck.file_type,
        "slide_count": deck.slide_count,
        "total_words": deck.total_words,
        "warnings": deck.warnings,
    }
    return payload


store = JobStore()

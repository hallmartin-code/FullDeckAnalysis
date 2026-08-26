"""Input validation, error types, text sanitisation and review quality control."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import List, Optional, Tuple

from models.schemas import Priority, ReviewResult

SUPPORTED_EXTENSIONS = {".pdf", ".pptx", ".ppt"}
MAX_FILE_BYTES = 200 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Error types
# --------------------------------------------------------------------------- #

class DeckReviewError(Exception):
    """Base class for every error this application reports to the user."""

    hint: str = ""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint or self.hint


class UnsupportedFileTypeError(DeckReviewError):
    pass


class FileAccessError(DeckReviewError):
    pass


class EncryptedFileError(DeckReviewError):
    pass


class CorruptFileError(DeckReviewError):
    pass


class ExtractionError(DeckReviewError):
    pass


class EmptyDeckError(DeckReviewError):
    pass


class AnalysisError(DeckReviewError):
    pass


class ReportError(DeckReviewError):
    pass


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #

def validate_input_file(path: str) -> Path:
    """Check the deck exists, is a file, is non-empty and has a supported extension."""
    p = Path(path).expanduser()
    if not p.exists():
        raise FileAccessError(
            "File not found: {}".format(p),
            "Check the path, or place the deck in the input/ folder.",
        )
    if p.is_dir():
        raise FileAccessError(
            "{} is a directory, not a pitch deck file.".format(p),
            "Pass the path to a single .pdf, .pptx or .ppt file.",
        )
    suffix = p.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            "Unsupported file type '{}'.".format(suffix or "(none)"),
            "Supported formats are PDF, PPTX and PPT.",
        )
    size = p.stat().st_size
    if size == 0:
        raise CorruptFileError(
            "File is empty (0 bytes): {}".format(p.name),
            "Re-export the deck and try again.",
        )
    if size > MAX_FILE_BYTES:
        raise FileAccessError(
            "File is {:.0f} MB, above the {:.0f} MB limit.".format(
                size / 1e6, MAX_FILE_BYTES / 1e6
            ),
            "Export a lighter version of the deck (compress images).",
        )
    return p


# --------------------------------------------------------------------------- #
# Text sanitisation -- prevents broken glyphs in the PDF report
# --------------------------------------------------------------------------- #

_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
    "•": "-", "●": "-", "▪": "-", "…": "...",
    "­": "",
}

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"[ \t]+")
_NEWLINES_RE = re.compile(r"\n{3,}")


def sanitize_text(text: Optional[str]) -> str:
    """Normalise unicode, expand ligatures and strip control characters."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    for src, dst in _LIGATURES.items():
        text = text.replace(src, dst)
    text = _CONTROL_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    text = _NEWLINES_RE.sub("\n\n", text)
    return text.strip()


def escape_for_reportlab(text: str) -> str:
    """ReportLab paragraphs are mini-HTML; neutralise markup characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# --------------------------------------------------------------------------- #
# Review quality control (spec section 18)
# --------------------------------------------------------------------------- #

_BANNED_VAGUE = (
    "great deck",
    "good job",
    "nice presentation",
    "looks good",
    "well done",
)


def validate_review(result: ReviewResult) -> Tuple[ReviewResult, List[str]]:
    """Clamp, de-duplicate and sanity-check a review before it is rendered.

    Returns the (mutated) result plus a list of human-readable QC issues. Issues are
    surfaced to the user rather than raised, so a partially weak analysis still
    produces a report.
    """
    issues: List[str] = []

    # Score must be an int in 0..100.
    try:
        score = int(round(float(result.presentation_score)))
    except (TypeError, ValueError):
        score = 0
        issues.append("Score was not numeric; defaulted to 0.")
    if not 0 <= score <= 100:
        issues.append("Score {} out of range; clamped to 0-100.".format(score))
        score = max(0, min(100, score))
    result.presentation_score = score

    # Assessment band must agree with the score.
    expected = assessment_for_score(score)
    if result.overall_assessment not in {"Strong", "Moderate", "Needs Work"}:
        result.overall_assessment = expected
    elif result.overall_assessment != expected:
        issues.append(
            "Assessment '{}' disagreed with score {}; using '{}'.".format(
                result.overall_assessment, score, expected
            )
        )
        result.overall_assessment = expected

    if not result.company or result.company.strip().lower() in {"", "unknown"}:
        result.company = "Unknown Company"
        issues.append("Company name could not be extracted from the deck.")

    # Sanitise every free-text surface.
    result.investment_thesis = sanitize_text(result.investment_thesis)
    result.presentation_analysis = sanitize_text(result.presentation_analysis)
    result.bottom_line = sanitize_text(result.bottom_line)
    result.flow_verdict = sanitize_text(result.flow_verdict)

    for finding in list(result.strengths) + list(result.weaknesses):
        finding.text = sanitize_text(finding.text)
        finding.why_investors_care = sanitize_text(finding.why_investors_care)
    for rec in result.priority_improvements:
        rec.text = sanitize_text(rec.text)
        rec.rationale = sanitize_text(rec.rationale)
        if rec.priority not in {p.value for p in Priority}:
            rec.priority = Priority.MEDIUM.value

    # Drop empties and near-duplicates while preserving order.
    result.strengths = _dedupe_findings(result.strengths)
    result.weaknesses = _dedupe_findings(result.weaknesses)
    result.priority_improvements = _dedupe_recs(result.priority_improvements)

    # Evidence-based strengths only.
    vague = [
        s.text for s in result.strengths
        if any(b in s.text.lower() for b in _BANNED_VAGUE) or len(s.text.split()) < 4
    ]
    if vague:
        issues.append("Removed {} generic strength(s) with no evidence.".format(len(vague)))
        result.strengths = [s for s in result.strengths if s.text not in vague]

    if not result.strengths:
        issues.append("No evidence-based strengths were identified.")
    if not result.weaknesses:
        issues.append("No weaknesses were identified; verify the analysis.")
    if not result.priority_improvements:
        issues.append("No recommendations were produced.")
    if not result.bottom_line:
        issues.append("Bottom line was empty.")

    # Ordering: HIGH first, then MEDIUM, then LOW.
    order = {Priority.HIGH.value: 0, Priority.MEDIUM.value: 1, Priority.LOW.value: 2}
    result.priority_improvements.sort(key=lambda r: order.get(r.priority, 3))

    result.warnings.extend(issues)
    return result, issues


def assessment_for_score(score: int) -> str:
    if score >= 75:
        return "Strong"
    if score >= 55:
        return "Moderate"
    return "Needs Work"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower())[:80]


def _dedupe_findings(items):
    seen, out = set(), []
    for item in items:
        if not item.text:
            continue
        key = _norm(item.text)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _dedupe_recs(items):
    seen, out = set(), []
    for item in items:
        if not item.text:
            continue
        key = _norm(item.text)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out

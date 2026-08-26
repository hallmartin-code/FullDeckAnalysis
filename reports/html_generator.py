"""Optional HTML/CSS -> PDF rendering path (Jinja2 + WeasyPrint).

ReportLab is the default engine because it has no system dependencies. WeasyPrint
produces slightly finer typography but needs GTK/Pango libraries that are awkward to
install on Windows, so this path is opt-in via ``--engine html`` and reports a clear
message when the dependencies are absent.

The same density levels and the same one-page verification loop as the ReportLab
generator are reused, so both engines honour the "exactly one page" contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from models.schemas import Priority, ReviewResult
from reports.pdf_generator import (
    SHORT_CATEGORY_LABELS,
    _bar_color,
    _flow_text,
    _levels,
    _meta_bit,
    _footer_text,
    _shorten,
)
from utils.validation import ReportError

TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_NAME = "investor_review.html"


def available() -> Tuple[bool, str]:
    """Return (usable, reason) for the HTML engine."""
    try:
        import jinja2  # noqa: F401
    except ImportError:
        return False, "Jinja2 is not installed (pip install jinja2)."
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError) as exc:
        return False, (
            "WeasyPrint is unavailable ({}). On Windows it needs the GTK runtime; "
            "the default --engine reportlab has no such dependency.".format(
                type(exc).__name__
            )
        )
    return True, ""


def generate_pdf(result: ReviewResult, destination: Path) -> Dict[str, Any]:
    usable, reason = available()
    if not usable:
        raise ReportError("Cannot use the HTML rendering engine.", reason)

    import jinja2
    from weasyprint import HTML

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=jinja2.select_autoescape(["html"]),
    )
    env.filters["shorten"] = _shorten
    template = env.get_template(TEMPLATE_NAME)

    attempts: List[Dict[str, Any]] = []
    for index, cfg in enumerate(_levels()):
        html = template.render(**_context(result, cfg))
        pdf_bytes = HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf()
        pages = _count_pages(pdf_bytes)
        attempts.append({"level": index, "pages": pages})
        if pages == 1:
            destination.write_bytes(pdf_bytes)
            return {
                "path": str(destination),
                "density_level": index,
                "attempts": attempts,
                "body_size": cfg["body_size"],
                "engine": "weasyprint",
            }

    raise ReportError(
        "Could not fit the review onto one page with the HTML engine.",
        "Try --engine reportlab.",
    )


def _context(result: ReviewResult, cfg: Dict[str, Any]) -> Dict[str, Any]:
    from markupsafe import Markup

    def tags(finding, config) -> Markup:
        parts = []
        if finding.slides:
            parts.append('<span class="tag">[s{}]</span>'.format(
                ",".join(str(s) for s in finding.slides[:3])))
        if finding.evidence in {"NOT FOUND", "INFERRED"}:
            parts.append('<span class="tag">[{}]</span>'.format(finding.evidence.lower()))
        if config["show_why"] and finding.why_investors_care:
            parts.append('<span class="why">{}</span>'.format(
                _shorten(finding.why_investors_care, 110)))
        return Markup(" " + " ".join(parts)) if parts else Markup("")

    groups = []
    for priority in (Priority.HIGH.value, Priority.MEDIUM.value, Priority.LOW.value):
        limit = cfg[priority.lower()]
        items = result.by_priority(priority)[:limit] if limit > 0 else []
        if items:
            groups.append((priority, items))

    meta_bits = [b for b in (_meta_bit(result.sector, 38), _meta_bit(result.stage, 24)) if b]
    meta_bits.append("{} slides".format(result.slide_count))

    return {
        "r": result,
        "cfg": cfg,
        "groups": groups,
        "tags": tags,
        "meta": " · ".join(meta_bits),
        "flow_text": _flow_text(result),
        "footer": _footer_text(result)[:190],
        "short_labels": SHORT_CATEGORY_LABELS,
        "bar_color": lambda score: _bar_color(float(score) / 100.0).hexval().replace("0x", "#"),
    }


def _count_pages(pdf_bytes: bytes) -> int:
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            # Fall back to counting page objects in the raw PDF.
            return max(1, pdf_bytes.count(b"/Type /Page") - pdf_bytes.count(b"/Type /Pages"))
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return doc.page_count
    finally:
        doc.close()

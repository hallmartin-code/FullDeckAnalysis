"""One-page investor review PDF, rendered with ReportLab.

Fitting strategy (spec sections 12 and 18): the report is built at a series of
*density levels*. Level 0 is the full report. Each subsequent level drops or trims
the least valuable content first -- supporting rationale, then LOW-priority
recommendations, then optional sections -- and only touches type size as a last
resort. After each attempt the document is rendered to memory and its real page
count checked, so "exactly one page" is verified rather than estimated.
"""

from __future__ import annotations

import datetime as _dt
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Table,
    TableStyle,
)

from models.schemas import Priority, ReviewResult
from utils.validation import ReportError, escape_for_reportlab

# --------------------------------------------------------------------------- #
# Palette and page geometry
# --------------------------------------------------------------------------- #

INK = colors.HexColor("#141821")
BODY = colors.HexColor("#2B313D")
MUTED = colors.HexColor("#6B7280")
RULE = colors.HexColor("#C9CDD4")
HAIRLINE = colors.HexColor("#E6E8EC")
ACCENT = colors.HexColor("#1F3A5F")
HIGH = colors.HexColor("#9E2B25")
MEDIUM = colors.HexColor("#9A6510")
LOW = colors.HexColor("#4B5563")
PANEL = colors.HexColor("#F4F5F7")

PAGE_W, PAGE_H = letter
MARGIN = 0.5 * inch
FRAME_W = PAGE_W - 2 * MARGIN

PRIORITY_COLORS = {
    Priority.HIGH.value: HIGH,
    Priority.MEDIUM.value: MEDIUM,
    Priority.LOW.value: LOW,
}

SHORT_CATEGORY_LABELS = {
    "Investment Thesis / Narrative": "THESIS",
    "Problem / Solution": "PROB/SOL",
    "Market Opportunity": "MARKET",
    "Traction": "TRACTION",
    "Business Model / GTM": "MODEL/GTM",
    "Competition / Defensibility": "COMPETE",
    "Financials / Raise": "FIN/RAISE",
    "Team": "TEAM",
    "Presentation / Visual Quality": "DESIGN",
}


# --------------------------------------------------------------------------- #
# Density levels -- ordered from richest to most compressed
# --------------------------------------------------------------------------- #

def _levels() -> List[Dict[str, Any]]:
    base = {
        "body_size": 8.6, "body_leading": 10.6,
        "strengths": 5, "concerns": 5, "high": 5, "medium": 3, "low": 2,
        "show_why": True, "show_rationale": True, "show_bars": True,
        "show_flow": True,
        "thesis_chars": 470, "analysis_chars": 360, "bottom_chars": 430,
        "finding_chars": 210, "rec_chars": 210,
        "placeholder": False,
    }
    levels = [dict(base)]

    def add(**changes):
        level = dict(levels[-1])
        level.update(changes)
        levels.append(level)

    # Ordered by what an investor loses least by losing. Each step is small so the
    # first level that fits leaves as little of the page unused as possible.
    add(low=1)
    add(low=0)
    add(show_rationale=False)
    add(concerns=4, strengths=5)
    add(strengths=4)
    add(finding_chars=175, rec_chars=180)
    add(show_why=False)
    add(thesis_chars=380, analysis_chars=300, bottom_chars=360)
    add(medium=2)
    add(show_flow=False)
    add(body_size=8.2, body_leading=10.0)
    add(strengths=3, concerns=3, high=4)
    add(show_bars=False, thesis_chars=280, analysis_chars=220, bottom_chars=260)
    add(body_size=7.8, body_leading=9.4, high=3, medium=2,
        finding_chars=135, rec_chars=140)
    return levels


# --------------------------------------------------------------------------- #
# Custom flowables
# --------------------------------------------------------------------------- #

class Rule(Flowable):
    """A horizontal divider with controlled space above and below."""

    def __init__(self, width: float, thickness: float = 0.6,
                 color=RULE, space_before: float = 4, space_after: float = 4):
        super().__init__()
        self.width = width
        self.thickness = thickness
        self.color = color
        self.space_before = space_before
        self.space_after = space_after
        self.height = thickness + space_before + space_after

    def draw(self) -> None:
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        y = self.space_after + self.thickness / 2.0
        self.canv.line(0, y, self.width, y)


class CategoryBars(Flowable):
    """Compact nine-category scorecard: label, mini bar, value."""

    def __init__(self, categories, width: float, height: float = 24.0,
                 placeholder: bool = False):
        super().__init__()
        self.categories = categories
        self.width = width
        self.height = height
        # Blank-form mode: draw the empty track and the weight, but no filled bar
        # and no number, so the scorecard reads as a field to complete.
        self.placeholder = placeholder

    def draw(self) -> None:
        if not self.categories:
            return
        canvas = self.canv
        count = len(self.categories)
        slot = self.width / count
        bar_w = slot - 7

        for index, category in enumerate(self.categories):
            x = index * slot
            pct = max(0.0, min(100.0, float(category.score))) / 100.0

            canvas.setFont("Helvetica-Bold", 5.1)
            canvas.setFillColor(MUTED)
            label = SHORT_CATEGORY_LABELS.get(category.category, category.category[:9].upper())
            canvas.drawString(x, self.height - 6.5, label)

            canvas.setFillColor(HAIRLINE)
            canvas.rect(x, self.height - 14.5, bar_w, 3.4, fill=1, stroke=0)
            if not self.placeholder:
                canvas.setFillColor(_bar_color(pct))
                canvas.rect(x, self.height - 14.5, bar_w * pct, 3.4, fill=1, stroke=0)

            canvas.setFont("Helvetica", 5.6)
            canvas.setFillColor(BODY)
            value = "0-100" if self.placeholder else "{:.0f}".format(category.score)
            canvas.drawString(x, self.height - 22.5,
                              "{}  ({} pts)".format(value, category.weight))


def _bar_color(pct: float):
    if pct >= 0.75:
        return colors.HexColor("#2F5D3A")
    if pct >= 0.5:
        return ACCENT
    if pct >= 0.3:
        return MEDIUM
    return HIGH


# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #

def _styles(cfg: Dict[str, Any]) -> Dict[str, ParagraphStyle]:
    size = cfg["body_size"]
    leading = cfg["body_leading"]
    return {
        "company": ParagraphStyle(
            "company", fontName="Helvetica-Bold", fontSize=17, leading=19,
            textColor=INK, spaceAfter=0,
        ),
        "kicker": ParagraphStyle(
            "kicker", fontName="Helvetica", fontSize=7.4, leading=9.4,
            textColor=MUTED, spaceBefore=2,
        ),
        "score": ParagraphStyle(
            "score", fontName="Helvetica-Bold", fontSize=21, leading=22,
            textColor=ACCENT, alignment=TA_RIGHT,
        ),
        "score_label": ParagraphStyle(
            "score_label", fontName="Helvetica", fontSize=7.2, leading=9,
            textColor=MUTED, alignment=TA_RIGHT,
        ),
        "section": ParagraphStyle(
            "section", fontName="Helvetica-Bold", fontSize=8.4, leading=10,
            textColor=ACCENT, spaceBefore=0, spaceAfter=3.4,
        ),
        "body": ParagraphStyle(
            "body", fontName="Helvetica", fontSize=size, leading=leading,
            textColor=BODY, alignment=TA_JUSTIFY,
        ),
        "bullet": ParagraphStyle(
            "bullet", fontName="Helvetica", fontSize=size, leading=leading,
            textColor=BODY, alignment=TA_LEFT, leftIndent=8.5, firstLineIndent=-8.5,
            spaceAfter=2.6,
        ),
        "rec": ParagraphStyle(
            "rec", fontName="Helvetica", fontSize=size, leading=leading,
            textColor=BODY, alignment=TA_LEFT, leftIndent=13, firstLineIndent=-13,
            spaceAfter=2.6,
        ),
        "priority": ParagraphStyle(
            "priority", fontName="Helvetica-Bold", fontSize=6.8, leading=8.4,
            textColor=MUTED, spaceBefore=1.5, spaceAfter=2,
        ),
        "panel": ParagraphStyle(
            "panel", fontName="Helvetica", fontSize=size, leading=leading + 0.4,
            textColor=INK, alignment=TA_JUSTIFY,
        ),
        "footer": ParagraphStyle(
            "footer", fontName="Helvetica", fontSize=6.2, leading=7.6, textColor=MUTED,
        ),
    }


# --------------------------------------------------------------------------- #
# Story construction
# --------------------------------------------------------------------------- #

def _build_story(result: ReviewResult, cfg: Dict[str, Any]) -> List[Any]:
    st = _styles(cfg)
    story: List[Any] = []

    story.extend(_header(result, st, cfg))
    story.append(Rule(FRAME_W, thickness=1.1, color=ACCENT, space_before=5, space_after=6))

    if cfg["show_bars"] and result.category_scores:
        story.append(CategoryBars(result.category_scores, FRAME_W,
                                  placeholder=cfg.get("placeholder", False)))
        story.append(Rule(FRAME_W, space_before=2, space_after=5))

    if result.investment_thesis:
        story.append(Paragraph("INVESTMENT THESIS", st["section"]))
        story.append(Paragraph(
            _esc(_shorten(result.investment_thesis, cfg["thesis_chars"])), st["body"]))
        story.append(Rule(FRAME_W, space_before=6, space_after=5))

    story.extend(_two_column_findings(result, cfg, st))
    story.extend(_recommendations(result, cfg, st))
    story.extend(_narrative_and_flow(result, cfg, st))
    story.extend(_bottom_line(result, cfg, st))

    return story


def _header(result: ReviewResult, st, cfg: Dict[str, Any]) -> List[Any]:
    # Keep the kicker to a single line: models sometimes return a whole sentence as
    # the stage, and a two-line kicker pushes the whole report down.
    meta_bits = [b for b in (_meta_bit(result.sector, 38), _meta_bit(result.stage, 24)) if b]
    count = "[N]" if cfg.get("placeholder") else str(result.slide_count)
    meta_bits.append("{} slides".format(count))
    kicker = "INVESTOR PRESENTATION REVIEW   ·   " + " · ".join(meta_bits)

    left = [
        Paragraph(_esc(result.company.upper()), st["company"]),
        Paragraph(_esc(kicker), st["kicker"]),
    ]
    score = "––" if cfg.get("placeholder") else str(result.presentation_score)
    right = [
        Paragraph("{}<font size=10> / 100</font>".format(score), st["score"]),
        Paragraph(_esc(result.overall_assessment.upper()), st["score_label"]),
    ]

    table = Table([[left, right]], colWidths=[FRAME_W * 0.68, FRAME_W * 0.32])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (0, 0), "TOP"),
        ("VALIGN", (1, 0), (1, 0), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [table]


def _two_column_findings(result: ReviewResult, cfg, st) -> List[Any]:
    strengths = result.strengths[: cfg["strengths"]]
    concerns = result.weaknesses[: cfg["concerns"]]
    if not strengths and not concerns:
        return []

    left = [Paragraph("KEY STRENGTHS", st["section"])]
    left.extend(_finding_paragraphs(strengths, cfg, st) or
                [Paragraph("<i>No evidence-based strengths identified.</i>", st["body"])])

    right = [Paragraph("KEY INVESTOR CONCERNS", st["section"])]
    right.extend(_finding_paragraphs(concerns, cfg, st) or
                 [Paragraph("<i>No material concerns identified.</i>", st["body"])])

    gutter = 16
    col_w = (FRAME_W - gutter) / 2.0
    table = Table([[left, right]], colWidths=[col_w, col_w])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), gutter / 2.0),
        ("LEFTPADDING", (1, 0), (1, 0), gutter / 2.0),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LINEBEFORE", (1, 0), (1, 0), 0.5, HAIRLINE),
    ]))
    return [table, Rule(FRAME_W, space_before=7, space_after=5)]


def _finding_paragraphs(findings, cfg, st) -> List[Any]:
    out = []
    for finding in findings:
        text = _esc(_shorten(finding.text, cfg["finding_chars"]))
        suffix = ""
        if finding.slides:
            suffix += ' <font color="#8A9099" size="{:.1f}">[s{}]</font>'.format(
                cfg["body_size"] - 1.4,
                ",".join(str(s) for s in finding.slides[:3]),
            )
        if finding.evidence in {"NOT FOUND", "INFERRED"}:
            suffix += ' <font color="#8A9099" size="{:.1f}">[{}]</font>'.format(
                cfg["body_size"] - 1.4, finding.evidence.lower()
            )
        if cfg["show_why"] and finding.why_investors_care:
            suffix += ' <font color="#6B7280"><i>{}</i></font>'.format(
                _esc(_shorten(finding.why_investors_care, 110))
            )
        out.append(Paragraph("–  {}{}".format(text, suffix), st["bullet"]))
    return out


def _recommendations(result: ReviewResult, cfg, st) -> List[Any]:
    groups = [
        (Priority.HIGH.value, cfg["high"]),
        (Priority.MEDIUM.value, cfg["medium"]),
        (Priority.LOW.value, cfg["low"]),
    ]
    selected = []
    for priority, limit in groups:
        if limit <= 0:
            continue
        items = result.by_priority(priority)[:limit]
        if items:
            selected.append((priority, items))
    if not selected:
        return []

    story: List[Any] = [Paragraph("PRIORITY IMPROVEMENTS", st["section"])]
    number = 1
    for priority, items in selected:
        story.append(Paragraph(
            '<font color="{}">{}</font>'.format(
                _hex(PRIORITY_COLORS[priority]), priority),
            st["priority"],
        ))
        for item in items:
            text = _esc(_shorten(item.text, cfg["rec_chars"]))
            if cfg["show_rationale"] and item.rationale:
                text += ' <font color="#6B7280"><i>{}</i></font>'.format(
                    _esc(_shorten(item.rationale, 100))
                )
            story.append(Paragraph(
                '<font color="{}"><b>{}.</b></font>  {}'.format(
                    _hex(PRIORITY_COLORS[priority]), number, text),
                st["rec"],
            ))
            number += 1
    story.append(Rule(FRAME_W, space_before=6, space_after=5))
    return story


def _narrative_and_flow(result: ReviewResult, cfg, st) -> List[Any]:
    analysis = _shorten(result.presentation_analysis, cfg["analysis_chars"])
    flow_text = _flow_text(result)

    if not analysis and not flow_text:
        return []

    left = [Paragraph("PRESENTATION &amp; STORYTELLING", st["section"])]
    left.append(Paragraph(_esc(analysis) if analysis else
                          "<i>Not assessed.</i>", st["body"]))

    if not cfg["show_flow"] or not flow_text:
        return [left[0], left[1], Rule(FRAME_W, space_before=6, space_after=5)]

    right = [
        Paragraph("RECOMMENDED FLOW", st["section"]),
        Paragraph(_esc(flow_text), st["body"]),
    ]
    gutter = 16
    col_w = (FRAME_W - gutter) / 2.0
    table = Table([[left, right]], colWidths=[col_w, col_w])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), gutter / 2.0),
        ("LEFTPADDING", (1, 0), (1, 0), gutter / 2.0),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LINEBEFORE", (1, 0), (1, 0), 0.5, HAIRLINE),
    ]))
    return [table, Rule(FRAME_W, space_before=7, space_after=5)]


def _flow_text(result: ReviewResult) -> str:
    if result.recommended_slide_flow:
        sequence = " → ".join(result.recommended_slide_flow)
        prefix = (result.flow_verdict + " ") if result.flow_verdict else ""
        return "{}Suggested sequence: {}".format(prefix, sequence)
    return result.flow_verdict or "Current flow is acceptable."


def _bottom_line(result: ReviewResult, cfg, st) -> List[Any]:
    if not result.bottom_line:
        return []
    text = "<b>BOTTOM LINE&nbsp;&nbsp;</b>{}".format(
        _esc(_shorten(result.bottom_line, cfg["bottom_chars"]))
    )
    table = Table([[Paragraph(text, st["panel"])]], colWidths=[FRAME_W])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT),
    ]))
    return [KeepTogether(table)]


# --------------------------------------------------------------------------- #
# Page furniture
# --------------------------------------------------------------------------- #

def _footer_text(result: ReviewResult) -> str:
    if result.analysis_source == "template":
        return ("Structure template. Bracketed text marks the content required in each "
                "field; no company data is present.")
    stamp = _dt.date.today().strftime("%d %b %Y")
    source = result.analysis_source or "unspecified"
    note = ("  |  Evidence-controlled review: claims marked [inferred] or [not found] "
            "are not stated in the deck.")
    return "Generated {} from the uploaded deck via {}.{}".format(stamp, source, note)


def _decorate(canvas, doc, result: ReviewResult) -> None:
    canvas.saveState()
    canvas.setStrokeColor(HAIRLINE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, MARGIN + 12, PAGE_W - MARGIN, MARGIN + 12)
    canvas.setFont("Helvetica", 6.2)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, MARGIN + 3.5, _footer_text(result)[:190])
    canvas.drawRightString(PAGE_W - MARGIN, MARGIN + 3.5, "Page 1 of 1")
    canvas.restoreState()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def generate_pdf(result: ReviewResult, destination: Path,
                 placeholder: bool = False) -> Dict[str, Any]:
    """Render the one-page review. Returns fit diagnostics.

    Raises :class:`ReportError` only if no density level produces a single page,
    which would indicate a bug rather than merely verbose content.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    attempts: List[Dict[str, Any]] = []
    chosen: Optional[Dict[str, Any]] = None
    levels = [dict(level, placeholder=placeholder) for level in _levels()]

    for index, cfg in enumerate(levels):
        pages = _render(result, cfg, BytesIO())
        attempts.append({"level": index, "pages": pages})
        if pages == 1:
            chosen = cfg
            break

    if chosen is None:
        # Last resort: the most compressed level with optional sections removed.
        chosen = dict(levels[-1])
        chosen.update({"show_flow": False, "show_bars": False, "show_why": False,
                       "show_rationale": False, "low": 0, "medium": 1, "high": 3,
                       "strengths": 3, "concerns": 3})
        pages = _render(result, chosen, BytesIO())
        attempts.append({"level": "fallback", "pages": pages})
        if pages != 1:
            raise ReportError(
                "Could not fit the review onto one page ({} pages at maximum "
                "compression).".format(pages),
                "This is unexpected -- please report the deck that triggered it.",
            )

    with open(destination, "wb") as handle:
        final_pages = _render(result, chosen, handle)

    if final_pages != 1:
        raise ReportError(
            "Final render produced {} pages instead of 1.".format(final_pages)
        )

    return {
        "path": str(destination),
        "density_level": attempts[-1]["level"],
        "attempts": attempts,
        "body_size": chosen["body_size"],
    }


def _render(result: ReviewResult, cfg: Dict[str, Any], target) -> int:
    """Build the document into ``target`` and return the page count actually produced."""
    doc = SimpleDocTemplate(
        target,
        pagesize=letter,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN + 16,
        title="{} - Investor Presentation Review".format(result.company),
        author="Investor Presentation Review",
        subject="One-page investor review of the uploaded pitch deck",
    )
    story = _build_story(result, cfg)
    decorate = lambda canvas, document: _decorate(canvas, document, result)  # noqa: E731
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return int(getattr(doc, "page", 1))


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #

def _meta_bit(value: Optional[str], limit: int) -> str:
    """One compact header fact: drop any parenthetical, then hard-cap the length."""
    text = (value or "").strip()
    if not text:
        return ""
    head = text.split(" (")[0].strip()
    if len(head) > limit:
        head = head[:limit].rstrip(" ,;:-")
    return head


def _hex(color) -> str:
    return "#" + color.hexval()[2:]


def _esc(text: str) -> str:
    return escape_for_reportlab(text or "")


def _shorten(text: str, max_chars: int) -> str:
    """Trim to a clean stopping point.

    Prefers a sentence boundary, which reads as finished. Falls back to a word
    boundary with an ellipsis. Never cuts at a comma -- "...addressable ARR," reads
    as a rendering bug rather than an abbreviation.
    """
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text

    window = text[:max_chars]
    for terminator in (". ", "; "):
        cut = window.rfind(terminator)
        if cut > max_chars * 0.5:
            return window[: cut + 1].rstrip()

    cut = window.rfind(" ")
    trimmed = window[:cut] if cut > 0 else window
    return trimmed.rstrip(" ,;:-—(") + "…"

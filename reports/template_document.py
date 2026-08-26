"""The blank structural template for the one-page Investor Presentation Review.

This module is the single definition of *what the report contains* -- every section,
every field, and the guidance for what belongs in each slot. It holds no company data
of any kind: each value is a placeholder describing the field it stands for.

Two uses:

1. ``python app.py --template`` renders it through the normal PDF generator, so the
   blank form is guaranteed to match the real report exactly -- same layout, same
   typography, same one-page fitting.
2. It documents the contract that every analysis provider must fill. The keys here
   are the keys of ``analysis.prompts.REVIEW_SCHEMA`` and of the emitted JSON.

The prose companion is ``reports/templates/report_structure.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from analysis.scoring import WEIGHTS
from models.schemas import (
    CategoryScore,
    Evidence,
    Finding,
    Priority,
    Recommendation,
    ReviewResult,
)

TEMPLATE_FILENAME = "investor_presentation_review_TEMPLATE"


# --------------------------------------------------------------------------- #
# Field placeholders -- what each slot must contain
# --------------------------------------------------------------------------- #

HEADER = {
    "company": "[COMPANY NAME]",
    "sector": "[Sector / sub-sector]",
    "stage": "[Stage]",
    "slide_count": 0,                    # rendered as "[N] slides"
    "presentation_score": 0,             # rendered as "__ / 100" in template mode
    "overall_assessment": "Strong / Moderate / Needs Work",
}

INVESTMENT_THESIS = (
    "[2-3 sentences, written for a partner who has not seen the deck: what the "
    "company does, for which customer, the problem it removes, the current stage and "
    "traction, the business model, the ask, and why this could be venture scale. "
    "If the deck does not support a thesis, state that plainly rather than "
    "constructing one.]"
)

STRENGTHS = [
    "[Strength 1 - the single most compelling, evidence-backed reason to keep "
    "reading. Cite the metric, customer, credential or milestone from the deck.]",
    "[Strength 2 - a distinct dimension: traction, team, market, differentiation, "
    "unit economics, regulatory progress or capital efficiency.]",
    "[Strength 3 - name the number or the proof point. Generic praise is removed "
    "automatically by the quality-control pass.]",
    "[Strength 4 - optional. Included when the page has room.]",
    "[Strength 5 - optional. Included when the page has room.]",
]

CONCERNS = [
    "[Concern 1 - the issue most likely to stop an investor taking the meeting. "
    "State the gap, not a general worry.]",
    "[Concern 2 - unsupported market sizing, unclear monetisation, missing "
    "traction, weak differentiation, team gap or an unfunded milestone plan.]",
    "[Concern 3 - information an investor expects that the deck never provides. "
    "Tagged [not found]; absence is a finding, never an assumed value.]",
    "[Concern 4 - optional. Included when the page has room.]",
    "[Concern 5 - optional. Included when the page has room.]",
]

WHY_INVESTORS_CARE = "[Why investors care: the consequence, in one clause.]"

HIGH_RECOMMENDATIONS = [
    "[HIGH 1 - a specific, actionable fix for the highest-severity gap. Name the "
    "slide, the replacement content and the calculation or evidence required.]",
    "[HIGH 2 - address the second issue that would materially reduce investor "
    "interest.]",
    "[HIGH 3 - tie the raise to measurable, value-creating milestones if it is not "
    "already tied.]",
    "[HIGH 4 - optional. Included when the page has room.]",
    "[HIGH 5 - optional. Included when the page has room.]",
]

MEDIUM_RECOMMENDATIONS = [
    "[MEDIUM 1 - an issue that weakens persuasion or credibility without blocking "
    "the meeting.]",
    "[MEDIUM 2 - a structural or evidentiary improvement to a specific slide.]",
    "[MEDIUM 3 - optional. Included when the page has room.]",
]

LOW_RECOMMENDATIONS = [
    "[LOW 1 - formatting, density, headline or typography polish on a named slide.]",
    "[LOW 2 - optional. Dropped first when the page is full.]",
]

RATIONALE = "[Why this matters, in one clause.]"

PRESENTATION_ANALYSIS = (
    "[2-3 sentences on narrative and design: does the story progress logically from "
    "problem through to the ask; are there jumps, repetition or mis-ordered slides; "
    "slide count, average words per slide, dense slides, image-only slides, small "
    "type, missing headlines, and which slides would overwhelm a reader.]"
)

FLOW_VERDICT = (
    "[One sentence. Either confirm the current flow is effective, or name the "
    "specific ordering problem.]"
)

RECOMMENDED_FLOW = [
    "[Leave empty if the current flow works]",
    "Investment thesis", "Problem", "Solution", "Product", "Why now", "Market",
    "Traction", "Business model", "Go-to-market", "Competition", "Defensibility",
    "Financials", "Team", "The ask", "Use of funds and milestones",
]

BOTTOM_LINE = (
    "[2-3 sentences, investor voice: would you take the meeting, on what evidence, "
    "and what single change would move the answer from 'meeting' to 'term sheet' - "
    "or from 'pass' to 'meeting'.]"
)

NOT_FOUND = [
    "[Each item of information an investor expects but the deck never provides.]",
    "[Reported as missing. Never converted into an assumed fact.]",
]


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

def build_template_result() -> ReviewResult:
    """A :class:`ReviewResult` whose every field is a placeholder."""
    result = ReviewResult(
        company=HEADER["company"],
        sector=HEADER["sector"],
        stage=HEADER["stage"],
        slide_count=HEADER["slide_count"],
        presentation_score=HEADER["presentation_score"],
        overall_assessment=HEADER["overall_assessment"],
        investment_thesis=INVESTMENT_THESIS,
        presentation_analysis=PRESENTATION_ANALYSIS,
        flow_verdict=FLOW_VERDICT,
        recommended_slide_flow=list(RECOMMENDED_FLOW),
        bottom_line=BOTTOM_LINE,
        not_found=list(NOT_FOUND),
        analysis_source="template",
    )

    result.strengths = [
        Finding(text=text, evidence=Evidence.FOUND.value,
                why_investors_care=WHY_INVESTORS_CARE)
        for text in STRENGTHS
    ]
    result.weaknesses = [
        Finding(
            text=text,
            # The third slot demonstrates how an absent expectation is rendered.
            evidence=Evidence.NOT_FOUND.value if index == 2 else Evidence.FOUND.value,
            why_investors_care=WHY_INVESTORS_CARE,
        )
        for index, text in enumerate(CONCERNS)
    ]

    result.priority_improvements = (
        [Recommendation(Priority.HIGH.value, t, RATIONALE) for t in HIGH_RECOMMENDATIONS]
        + [Recommendation(Priority.MEDIUM.value, t, RATIONALE) for t in MEDIUM_RECOMMENDATIONS]
        + [Recommendation(Priority.LOW.value, t, RATIONALE) for t in LOW_RECOMMENDATIONS]
    )

    result.category_scores = [
        CategoryScore(category=label, score=0.0, weight=weight,
                      note="[One-line justification for this score.]")
        for label, weight in WEIGHTS.values()
    ]
    return result


def template_json() -> Dict[str, Any]:
    """The JSON artifact's structure, with placeholders instead of findings."""
    return build_template_result().to_dict()


def write_template(output_dir: Path, engine: str = "reportlab") -> Dict[str, Path]:
    """Render the blank template as PDF and JSON into ``output_dir``."""
    from utils.file_utils import ensure_output_dir, write_json

    out = ensure_output_dir(str(output_dir))
    result = build_template_result()

    if engine == "html":
        from reports import html_generator

        info = html_generator.generate_pdf(result, out / (TEMPLATE_FILENAME + ".pdf"))
    else:
        from reports.pdf_generator import generate_pdf

        info = generate_pdf(result, out / (TEMPLATE_FILENAME + ".pdf"), placeholder=True)

    return {
        "pdf": Path(info["path"]),
        "json": write_json(result.to_dict(), out / (TEMPLATE_FILENAME + ".json")),
    }

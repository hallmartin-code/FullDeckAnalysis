"""Typed data structures shared across extraction, analysis and reporting.

Three layers:

1. :class:`ExtractedDeck` -- raw, loss-tolerant capture of whatever the file gave us.
2. :class:`DeckProfile`   -- the normalized company/deal representation (spec section 4).
3. :class:`ReviewResult`  -- the investor review that gets rendered to PDF/JSON.

Everything is a plain dataclass with ``to_dict()`` so the JSON artifact is a direct
serialization of the in-memory model, with no separate mapping layer to drift.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Evidence control (spec section 17)
# --------------------------------------------------------------------------- #

class Evidence(str, Enum):
    """How a claim in the review relates to the source deck."""

    FOUND = "FOUND IN DECK"
    INFERRED = "INFERRED"
    NOT_FOUND = "NOT FOUND"
    UNCLEAR = "UNCLEAR"


NOT_FOUND = "NOT FOUND"
UNCLEAR = "UNCLEAR"


class Priority(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# --------------------------------------------------------------------------- #
# Layer 1 -- extraction
# --------------------------------------------------------------------------- #

@dataclass
class Slide:
    """One page (PDF) or slide (PPTX)."""

    index: int                                   # 1-based, presentation order
    title: str = ""
    text: str = ""
    notes: str = ""                              # speaker notes (pptx only)
    tables: List[List[List[str]]] = field(default_factory=list)
    chart_labels: List[str] = field(default_factory=list)
    image_count: int = 0
    word_count: int = 0
    is_text_sparse: bool = False                 # image-heavy / little extractable text

    def __post_init__(self) -> None:
        if not self.word_count:
            self.word_count = len(self.text.split())
        self.is_text_sparse = self.word_count < 12 and self.image_count > 0

    @property
    def combined_text(self) -> str:
        """Title + body + flattened tables + chart labels, for keyword scanning."""
        parts = [self.title, self.text]
        for table in self.tables:
            parts.extend(" ".join(str(c) for c in row) for row in table)
        parts.extend(self.chart_labels)
        return "\n".join(p for p in parts if p)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractedDeck:
    """Raw extraction output. ``warnings`` records anything degraded, never silently."""

    source_path: str
    file_type: str                               # "pdf" | "pptx" | "ppt"
    slides: List[Slide] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    extractor: str = ""

    @property
    def slide_count(self) -> int:
        return len(self.slides)

    @property
    def full_text(self) -> str:
        return "\n\n".join(
            "--- SLIDE {} ---\n{}".format(s.index, s.combined_text) for s in self.slides
        )

    @property
    def total_words(self) -> int:
        return sum(s.word_count for s in self.slides)

    @property
    def image_only_slides(self) -> List[int]:
        return [s.index for s in self.slides if s.is_text_sparse]

    @property
    def has_usable_text(self) -> bool:
        """Enough text to review, rather than a scanned or image-only deck."""
        return self.total_words >= 60

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_path": self.source_path,
            "file_type": self.file_type,
            "extractor": self.extractor,
            "slide_count": self.slide_count,
            "total_words": self.total_words,
            "warnings": self.warnings,
            "metadata": self.metadata,
            "slides": [s.to_dict() for s in self.slides],
        }


# --------------------------------------------------------------------------- #
# Layer 2 -- normalized deck profile (spec section 4)
# --------------------------------------------------------------------------- #

@dataclass
class Market:
    tam: Optional[str] = None
    sam: Optional[str] = None
    som: Optional[str] = None
    methodology: Optional[str] = None            # "top-down" | "bottom-up" | None


@dataclass
class Financials:
    revenue: Optional[str] = None
    gross_margin: Optional[str] = None
    burn: Optional[str] = None
    runway: Optional[str] = None
    projections: Optional[str] = None


@dataclass
class Fundraising:
    raise_amount: Optional[str] = None
    valuation: Optional[str] = None
    security: Optional[str] = None
    committed: Optional[str] = None
    use_of_funds: List[str] = field(default_factory=list)


@dataclass
class DeckProfile:
    """Only fields supported by the deck are populated; the rest stay None or empty."""

    company_name: Optional[str] = None
    sector: Optional[str] = None
    stage: Optional[str] = None
    location: Optional[str] = None
    problem: Optional[str] = None
    solution: Optional[str] = None
    product: Optional[str] = None
    market: Market = field(default_factory=Market)
    business_model: Optional[str] = None
    revenue_model: Optional[str] = None
    traction: List[str] = field(default_factory=list)
    customers: List[str] = field(default_factory=list)
    growth_metrics: List[str] = field(default_factory=list)
    financials: Financials = field(default_factory=Financials)
    competition: List[str] = field(default_factory=list)
    competitive_advantage: Optional[str] = None
    go_to_market: Optional[str] = None
    team: List[str] = field(default_factory=list)
    fundraising: Fundraising = field(default_factory=Fundraising)
    milestones: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    slide_count: int = 0

    # Derived signals used by scoring and prompting, not part of the spec schema.
    detected_sections: Dict[str, List[int]] = field(default_factory=dict)
    missing_sections: List[str] = field(default_factory=list)
    slide_order: List[str] = field(default_factory=list)
    design_signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Layer 3 -- review output
# --------------------------------------------------------------------------- #

@dataclass
class Finding:
    """A strength or a weakness. ``evidence`` ties it back to the deck."""

    text: str
    evidence: str = Evidence.FOUND.value
    slides: List[int] = field(default_factory=list)
    why_investors_care: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Recommendation:
    priority: str                                # HIGH | MEDIUM | LOW
    text: str
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CategoryScore:
    category: str
    score: float
    weight: int
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewResult:
    company: str = "Unknown Company"
    sector: Optional[str] = None
    stage: Optional[str] = None
    slide_count: int = 0
    presentation_score: int = 0
    overall_assessment: str = ""                 # Strong | Moderate | Needs Work
    investment_thesis: str = ""
    strengths: List[Finding] = field(default_factory=list)
    weaknesses: List[Finding] = field(default_factory=list)
    priority_improvements: List[Recommendation] = field(default_factory=list)
    presentation_analysis: str = ""
    recommended_slide_flow: List[str] = field(default_factory=list)
    flow_verdict: str = ""
    bottom_line: str = ""
    category_scores: List[CategoryScore] = field(default_factory=list)
    not_found: List[str] = field(default_factory=list)
    analysis_source: str = ""                    # which LLM provider produced this
    warnings: List[str] = field(default_factory=list)

    def by_priority(self, priority: str) -> List[Recommendation]:
        return [r for r in self.priority_improvements if r.priority == priority]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "company": self.company,
            "sector": self.sector,
            "stage": self.stage,
            "slide_count": self.slide_count,
            "presentation_score": self.presentation_score,
            "overall_assessment": self.overall_assessment,
            "investment_thesis": self.investment_thesis,
            "strengths": [s.to_dict() for s in self.strengths],
            "weaknesses": [w.to_dict() for w in self.weaknesses],
            "priority_improvements": [r.to_dict() for r in self.priority_improvements],
            "presentation_analysis": self.presentation_analysis,
            "recommended_slide_flow": self.recommended_slide_flow,
            "flow_verdict": self.flow_verdict,
            "bottom_line": self.bottom_line,
            "category_scores": [c.to_dict() for c in self.category_scores],
            "not_found": self.not_found,
            "analysis_source": self.analysis_source,
            "warnings": self.warnings,
        }


def slugify(name: str) -> str:
    """Filesystem-safe stem for output files."""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "company").lower()).strip("_")
    return slug or "company"

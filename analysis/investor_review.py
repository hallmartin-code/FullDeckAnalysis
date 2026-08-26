"""Orchestration: extracted deck -> normalized profile -> provider -> validated review."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from analysis.deck_parser import parse_deck
from analysis.llm_provider import LLMProvider, get_provider
from analysis.scoring import reconcile, total_score
from models.schemas import (
    DeckProfile,
    Evidence,
    ExtractedDeck,
    Finding,
    Priority,
    Recommendation,
    ReviewResult,
)
from utils.validation import AnalysisError, EmptyDeckError, sanitize_text, validate_review

MIN_SLIDES = 3

# Currency / percentage tokens we can verify against the deck text.
_FIGURE_RE = re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d+)?\s?(?:billion|million|bn|mm|[BMKT])?|\b\d{1,3}(?:\.\d+)?%")


def review_deck(deck: ExtractedDeck, provider: Optional[LLMProvider] = None,
                provider_name: Optional[str] = None,
                model: Optional[str] = None) -> ReviewResult:
    """Run the full analysis pipeline and return a validated :class:`ReviewResult`."""
    if deck.slide_count < MIN_SLIDES:
        raise EmptyDeckError(
            "The deck has only {} slide(s).".format(deck.slide_count),
            "A meaningful investor review needs at least {} slides.".format(MIN_SLIDES),
        )

    profile = parse_deck(deck)
    provider = provider or get_provider(provider_name, model)

    raw = provider.analyze_deck(deck, profile)
    if not isinstance(raw, dict):
        raise AnalysisError(
            "Provider '{}' returned {}, not a review object.".format(
                provider.name, type(raw).__name__
            )
        )

    result = _build_result(raw, deck, profile, provider)
    result, _ = validate_review(result)
    return result


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

def _build_result(raw: Dict[str, Any], deck: ExtractedDeck, profile: DeckProfile,
                  provider: LLMProvider) -> ReviewResult:
    result = ReviewResult(
        company=_clean(raw.get("company")) or profile.company_name or "Unknown Company",
        sector=_clean(raw.get("sector")) or profile.sector,
        stage=_clean(raw.get("stage")) or profile.stage,
        slide_count=deck.slide_count,
        investment_thesis=_clean(raw.get("investment_thesis")),
        presentation_analysis=_clean(raw.get("presentation_analysis")),
        flow_verdict=_clean(raw.get("flow_verdict")),
        bottom_line=_clean(raw.get("bottom_line")),
        recommended_slide_flow=[_clean(s) for s in _as_list(raw.get("recommended_slide_flow"))][:16],
        not_found=[_clean(s) for s in _as_list(raw.get("not_found"))][:12],
        analysis_source="{}{}".format(
            provider.name,
            ":" + provider.model if getattr(provider, "model", None) else "",
        ),
        warnings=list(deck.warnings),
    )

    result.strengths = _findings(raw.get("strengths"), deck)
    result.weaknesses = _findings(raw.get("weaknesses"), deck)
    result.priority_improvements = _recommendations(raw.get("priority_improvements"))

    # Passed through untouched: reconcile accepts both the object and list forms, and
    # wrapping the object in a list would hide every score the model produced.
    result.category_scores = reconcile(raw.get("category_scores"), profile)
    result.presentation_score = total_score(result.category_scores)

    _evidence_check(result, deck)

    # If the deck was barely readable, say so on the report rather than scoring it
    # as though the content had been assessed.
    if not deck.has_usable_text:
        result.warnings.append(
            "Deck is image-only or near-empty; content analysis is structural only."
        )
    return result


def _findings(items: Any, deck: ExtractedDeck) -> List[Finding]:
    out: List[Finding] = []
    valid_evidence = {e.value for e in Evidence}
    for item in _as_list(items):
        if isinstance(item, str):
            out.append(Finding(text=sanitize_text(item)))
            continue
        if not isinstance(item, dict):
            continue
        text = _clean(item.get("text"))
        if not text:
            continue
        evidence = str(item.get("evidence") or Evidence.FOUND.value).upper()
        if evidence not in valid_evidence:
            evidence = Evidence.INFERRED.value
        slides = [
            n for n in _as_list(item.get("slides"))
            if isinstance(n, int) and 1 <= n <= deck.slide_count
        ]
        out.append(Finding(
            text=text,
            evidence=evidence,
            slides=slides[:5],
            why_investors_care=_clean(item.get("why_investors_care")),
        ))
    return out[:8]


def _recommendations(items: Any) -> List[Recommendation]:
    out: List[Recommendation] = []
    valid = {p.value for p in Priority}
    for item in _as_list(items):
        if isinstance(item, str):
            out.append(Recommendation(priority=Priority.MEDIUM.value, text=sanitize_text(item)))
            continue
        if not isinstance(item, dict):
            continue
        text = _clean(item.get("text"))
        if not text:
            continue
        priority = str(item.get("priority") or "").upper()
        out.append(Recommendation(
            priority=priority if priority in valid else Priority.MEDIUM.value,
            text=text,
            rationale=_clean(item.get("rationale")),
        ))
    return out[:10]


# --------------------------------------------------------------------------- #
# Evidence control (spec section 17)
# --------------------------------------------------------------------------- #

def _evidence_check(result: ReviewResult, deck: ExtractedDeck) -> None:
    """Downgrade FOUND-IN-DECK claims whose figures do not appear in the deck.

    This catches the most damaging hallucination class -- an invented number stated
    as fact -- without trying to fact-check prose. A claim that cites a figure the
    deck never contains is relabelled INFERRED and flagged in the warnings.
    """
    haystack = _normalise_figures(deck.full_text)
    flagged: List[str] = []

    for finding in list(result.strengths) + list(result.weaknesses):
        if finding.evidence != Evidence.FOUND.value:
            continue
        figures = _FIGURE_RE.findall(finding.text)
        missing = [f for f in figures if _normalise_figures(f) not in haystack]
        if missing:
            finding.evidence = Evidence.INFERRED.value
            flagged.append("{} (figure not located in deck text)".format(missing[0]))

    if flagged:
        result.warnings.append(
            "Evidence check relabelled {} claim(s) as INFERRED: {}".format(
                len(flagged), "; ".join(flagged[:3])
            )
        )


def _normalise_figures(text: str) -> str:
    """Strip spacing/commas/case so '$1.2 M' matches '$1.2M'."""
    return re.sub(r"[\s,]", "", (text or "").lower())


# --------------------------------------------------------------------------- #
# Coercion helpers
# --------------------------------------------------------------------------- #

def _clean(value: Any) -> str:
    if value is None:
        return ""
    return sanitize_text(str(value))


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]

"""Presentation Effectiveness Score (spec section 11).

Nine weighted categories summing to 100. Each category is scored 0-100 as a
percentage of its own weight, then contributes ``weight * pct / 100`` points.

Two producers exist:

* :func:`heuristic_category_scores` -- deterministic, derived purely from what the
  parser could find. Used on its own when no LLM is configured, and as a sanity
  band for LLM output.
* :func:`reconcile` -- validates LLM-supplied category scores, fills any the model
  omitted from the heuristic, and recomputes the total so the headline number
  always equals the sum of its parts.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from models.schemas import CategoryScore, DeckProfile

# category key -> (display label, weight)
WEIGHTS: Dict[str, tuple] = {
    "thesis_narrative": ("Investment Thesis / Narrative", 15),
    "problem_solution": ("Problem / Solution", 15),
    "market": ("Market Opportunity", 10),
    "traction": ("Traction", 15),
    "model_gtm": ("Business Model / GTM", 15),
    "competition": ("Competition / Defensibility", 10),
    "financials_raise": ("Financials / Raise", 10),
    "team": ("Team", 5),
    "presentation": ("Presentation / Visual Quality", 5),
}

TOTAL_WEIGHT = sum(w for _, w in WEIGHTS.values())  # 100

# Ceiling applied to content categories in the offline heuristic pass. See the
# comment in heuristic_category_scores for why full marks are withheld there.
HEURISTIC_CEILING = 78.0


def heuristic_category_scores(profile: DeckProfile) -> List[CategoryScore]:
    """Score each category from parser signals alone."""
    sections = profile.detected_sections
    design = profile.design_signals or {}
    pct: Dict[str, float] = {}
    notes: Dict[str, str] = {}

    # --- Investment thesis / narrative ------------------------------------- #
    value = 30.0
    if sections.get("thesis"):
        value += 25
    if sections.get("why_now"):
        value += 10
    ordered, inversions = flow_is_ordered(profile.slide_order)
    value += 25 if ordered else max(0, 20 - inversions * 5)
    if profile.company_name:
        value += 10
    pct["thesis_narrative"] = value
    notes["thesis_narrative"] = (
        "Narrative follows the expected investor sequence."
        if ordered else
        "{} out-of-sequence transition(s) in the narrative.".format(inversions)
    )

    # --- Problem / solution ------------------------------------------------- #
    value = 20.0
    if sections.get("problem"):
        value += 30
    if sections.get("solution"):
        value += 30
    if sections.get("product"):
        value += 20
    pct["problem_solution"] = value
    notes["problem_solution"] = _presence_note(
        [("problem", sections.get("problem")), ("solution", sections.get("solution"))]
    )

    # --- Market ------------------------------------------------------------- #
    value = 15.0
    market = profile.market
    if market.tam:
        value += 30
    if market.sam:
        value += 20
    if market.som:
        value += 10
    if market.methodology == "bottom-up":
        value += 25
    elif market.methodology == "top-down":
        value -= 5
    pct["market"] = value
    notes["market"] = (
        "Bottom-up sizing present." if market.methodology == "bottom-up"
        else "Top-down sizing only." if market.methodology == "top-down"
        else "Market sizing not found."
    )

    # --- Traction ----------------------------------------------------------- #
    value = 10.0
    value += min(45, len(profile.traction) * 9)
    if profile.financials.revenue:
        value += 20
    if profile.customers:
        value += 15
    if profile.growth_metrics:
        value += 10
    pct["traction"] = value
    notes["traction"] = "{} quantified traction data point(s).".format(len(profile.traction))

    # --- Business model / GTM ---------------------------------------------- #
    value = 15.0
    if sections.get("business_model"):
        value += 25
    if profile.revenue_model:
        value += 20
    if sections.get("gtm"):
        value += 25
    if profile.financials.gross_margin:
        value += 15
    pct["model_gtm"] = value
    notes["model_gtm"] = _presence_note(
        [("business model", sections.get("business_model")), ("GTM", sections.get("gtm"))]
    )

    # --- Competition / defensibility ---------------------------------------- #
    value = 15.0
    if sections.get("competition"):
        value += 30
    if profile.competition:
        value += 20
    if sections.get("defensibility"):
        value += 25
    if profile.competitive_advantage:
        value += 10
    pct["competition"] = value
    notes["competition"] = _presence_note(
        [("competition", sections.get("competition")),
         ("defensibility", sections.get("defensibility"))]
    )

    # --- Financials / raise -------------------------------------------------- #
    value = 10.0
    fundraising = profile.fundraising
    if fundraising.raise_amount:
        value += 25
    if fundraising.use_of_funds:
        value += 20
    if fundraising.security or fundraising.valuation:
        value += 15
    if sections.get("financials"):
        value += 20
    if profile.financials.runway or profile.financials.burn:
        value += 10
    pct["financials_raise"] = value
    notes["financials_raise"] = (
        "Ask stated." if fundraising.raise_amount else "Fundraising ask not stated."
    )

    # --- Team ---------------------------------------------------------------- #
    value = 15.0
    if sections.get("team"):
        value += 35
    value += min(50, len(profile.team) * 12)
    pct["team"] = value
    notes["team"] = "{} team member(s) identified.".format(len(profile.team))

    # --- Presentation / visual quality --------------------------------------- #
    value = 75.0
    avg_words = float(design.get("avg_words_per_slide") or 0)
    if avg_words > 90:
        value -= 25
    elif avg_words > 60:
        value -= 12
    value -= min(20, len(design.get("very_dense_slides") or []) * 7)
    value -= min(10, len(design.get("small_font_slides") or []) * 4)
    value -= min(10, len(design.get("slides_without_titles") or []) * 3)
    slide_count = profile.slide_count
    if slide_count and not 10 <= slide_count <= 25:
        value -= 8
    pct["presentation"] = value
    notes["presentation"] = "Average {:.0f} words per slide across {} slides.".format(
        avg_words, slide_count
    )

    # A rule-based pass can only observe that a section exists and how densely it is
    # written -- never whether the argument on it is any good. Content categories are
    # therefore capped below full marks so an offline run cannot report false
    # confidence. Presentation quality is genuinely measured, so it is not capped.
    for key in pct:
        if key != "presentation":
            pct[key] = min(pct[key], HEURISTIC_CEILING)

    return [
        CategoryScore(
            category=WEIGHTS[key][0],
            score=round(_clamp(pct[key]), 1),
            weight=WEIGHTS[key][1],
            note=notes.get(key, ""),
        )
        for key in WEIGHTS
    ]


def flow_is_ordered(slide_order: List[str]) -> tuple:
    """Count how often the deck moves backwards through the canonical narrative."""
    from analysis.deck_parser import CANONICAL_FLOW

    ranks = [CANONICAL_FLOW.index(s) for s in slide_order if s in CANONICAL_FLOW]
    inversions = sum(1 for a, b in zip(ranks, ranks[1:]) if b < a - 1)
    return inversions == 0, inversions


def total_score(categories: List[CategoryScore]) -> int:
    """Weighted total, 0-100."""
    if not categories:
        return 0
    points = sum(_clamp(c.score) / 100.0 * c.weight for c in categories)
    weight = sum(c.weight for c in categories) or TOTAL_WEIGHT
    return int(round(points / weight * 100))


def reconcile(llm_categories, profile: DeckProfile) -> List[CategoryScore]:
    """Validate LLM category scores; fall back to the heuristic for any that are bad.

    Accepts the canonical object form ``{"traction": 62, ...}`` and also tolerates a
    list of ``{"category", "score", "note"}`` entries, which is what some providers
    produce. Notes always come from the heuristic pass, since they describe the
    structural evidence rather than the model's opinion.
    """
    baseline = {c.category: c for c in heuristic_category_scores(profile)}
    if not llm_categories:
        return list(baseline.values())

    scores: Dict[str, float] = {}

    if isinstance(llm_categories, dict):
        for key, value in llm_categories.items():
            if key in WEIGHTS:
                parsed = _to_score(value)
                if parsed is not None:
                    scores[key] = parsed
    else:
        by_label = {label: key for key, (label, _) in WEIGHTS.items()}
        for entry in llm_categories:
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("category", "")).strip()
            key = by_label.get(label) or (label if label in WEIGHTS else None)
            if key is None and label:
                key = next((k for lbl, k in by_label.items()
                            if lbl.lower().startswith(label.lower()[:8])), None)
            if key is None:
                continue
            parsed = _to_score(entry.get("score"))
            if parsed is not None:
                scores[key] = parsed

    out: List[CategoryScore] = []
    for key, (label, weight) in WEIGHTS.items():
        if key in scores:
            out.append(CategoryScore(
                category=label,
                score=round(scores[key], 1),
                weight=weight,
                note=baseline[label].note,
            ))
        else:
            out.append(baseline[label])
    return out


def _to_score(value) -> Optional[float]:
    try:
        return _clamp(float(value))
    except (TypeError, ValueError):
        return None


def _presence_note(pairs) -> str:
    missing = [name for name, indices in pairs if not indices]
    if not missing:
        return "All expected sections present."
    return "No dedicated {} slide.".format(" or ".join(missing))


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))

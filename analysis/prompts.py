"""System prompt, user prompt construction and the structured-output JSON schema."""

from __future__ import annotations

import json
from typing import Any, Dict

from analysis.scoring import WEIGHTS
from models.schemas import DeckProfile, ExtractedDeck

CATEGORY_LABELS = [label for label, _ in WEIGHTS.values()]
CATEGORY_SPEC = "\n".join(
    "    {} = {} ({} pts)".format(key, label, weight)
    for key, (label, weight) in WEIGHTS.items()
)

SYSTEM_PROMPT = """You are a Seed to Series A venture investor reviewing a startup's \
pitch deck before deciding whether to take a first meeting. You have seen thousands of \
decks. You are direct, specific and commercially minded.

Your job is not to summarise the slides. It is to judge how effectively this deck \
communicates an investable opportunity, and to tell the founder exactly what to fix first.

Hold the line between two different things:
  - what the company SAYS in the deck, and
  - what an investor NEEDS TO BELIEVE to write a cheque.

EVIDENCE RULES - these are absolute:
1. Every strength must cite something actually in the deck. Name the number, the \
customer, the credential or the slide. Never write generic praise such as "strong team" \
or "large market" without the supporting detail from the deck.
2. Never fabricate revenue, customers, market size, valuation, funding, partnerships, \
regulatory progress, IP, financial metrics or team experience. If it is not in the \
deck, it does not exist for the purposes of this review.
3. Mark each finding's `evidence` field as one of:
   - "FOUND IN DECK" - explicitly stated in the deck.
   - "INFERRED" - a reasonable reading of the deck, not stated outright. Use hedged \
language in the text itself.
   - "NOT FOUND" - something an investor expects that is absent. Absence is a valid, \
important finding; it is never a licence to assume a value.
4. Populate `slides` with the 1-based slide numbers that support the finding. Leave it \
empty for NOT FOUND items.
5. Missing information must be reported as missing. Do not convert it into an assumed fact.

Treat "we have no competitors" as a weakness unless the deck justifies it convincingly.
Treat a top-down-only TAM as a weakness. Treat a raise that is not tied to measurable \
value-creating milestones as a weakness.

Recommendations must be concrete enough to act on this week. Write "Replace the $25B \
top-down TAM with a bottom-up build: target hospitals x annual contract value", not \
"improve the market slide".

The output must fit on one printed page, so be economical. Prefer six sharp findings \
over twelve soft ones."""


def build_user_prompt(deck: ExtractedDeck, profile: DeckProfile,
                      max_chars: int = 180_000) -> str:
    """Assemble the review request: deck text plus mechanical extraction context."""
    deck_text = deck.full_text
    truncation_note = ""
    if len(deck_text) > max_chars:
        deck_text = deck_text[:max_chars]
        truncation_note = (
            "\n\n[NOTE: the deck text exceeded the prompt budget and was cut at "
            "{} characters. Say so in your presentation analysis.]".format(max_chars)
        )

    notes = _extraction_notes(deck)
    profile_json = json.dumps(_profile_context(profile), indent=2, ensure_ascii=False)

    return """Review the pitch deck below.

=== DECK FACTS ===
Source format: {file_type}
Slides: {slide_count}
Total extractable words: {total_words}
{notes}

=== MECHANICAL EXTRACTION (advisory, may be incomplete or wrong) ===
A deterministic parser produced the following. Use it as a hint about structure and
design density, but read the deck text yourself and trust the deck over this summary.

{profile_json}

=== DECK TEXT (in slide order) ===
{deck_text}{truncation_note}

=== YOUR TASK ===
Produce the structured investor review. Requirements:

- `company`: the company name exactly as the deck presents it. If the deck never names
  it, use "Unknown Company".
- `investment_thesis`: 2-3 sentences an investor would write to a partner who has not
  seen the deck. Cover what the company does, for whom, and why it could be venture
  scale. If the deck does not support a thesis, say that plainly.
- `category_scores`: an object with exactly these nine keys. Each value is a number
  from 0 to 100 giving the percentage of that category's own weight the deck earns:
{categories}
  Use the whole range. A deck with no traction slide scores near 0 on traction.
- `strengths`: 3-5 findings, each evidence-based and specific. `evidence` must be
  exactly one of "FOUND IN DECK", "INFERRED" or "NOT FOUND".
- `weaknesses`: 3-6 findings, same `evidence` values. For each, `why_investors_care`
  states the investor consequence in one clause.
- `priority_improvements`: 5-8 recommendations. `priority` must be exactly "HIGH",
  "MEDIUM" or "LOW". Use HIGH for issues that would materially reduce investor
  interest, MEDIUM for issues that weaken persuasion or credibility, LOW for polish.
  Include at least three HIGH unless the deck is genuinely strong.
- `presentation_analysis`: 2-3 sentences on narrative, storytelling and visual/design
  effectiveness, grounded in the density signals above.
- `flow_verdict`: one short sentence. Either state that the current flow works, or name
  the specific ordering problem.
- `recommended_slide_flow`: leave empty if the current flow is effective. Otherwise give
  an abbreviated revised sequence of 8-12 slide names.
- `not_found`: the information an investor expects that is absent from the deck.
- `bottom_line`: 2-3 sentences. Would you take the meeting, and what single thing would
  change your answer?

Be honest. A weak deck should score below 50.""".format(
        file_type=deck.file_type.upper(),
        slide_count=deck.slide_count,
        total_words=deck.total_words,
        notes=notes,
        profile_json=profile_json,
        deck_text=deck_text,
        truncation_note=truncation_note,
        categories=CATEGORY_SPEC,
    )


def _extraction_notes(deck: ExtractedDeck) -> str:
    lines = []
    if deck.warnings:
        lines.append("Extraction warnings (factor these into the presentation analysis):")
        lines.extend("  - " + w for w in deck.warnings)
    if deck.image_only_slides:
        lines.append(
            "  - Slides with images but almost no extractable text: {}. Their content "
            "is unknown to you; do not guess what they contain.".format(
                ", ".join(str(i) for i in deck.image_only_slides)
            )
        )
    return "\n".join(lines) if lines else "No extraction problems."


def _profile_context(profile: DeckProfile) -> Dict[str, Any]:
    data = profile.to_dict()
    # Drop the free-text section dumps -- the model reads the raw deck for those.
    for key in ("problem", "solution", "product", "business_model", "go_to_market",
                "competitive_advantage"):
        if data.get(key):
            data[key] = "(present -- see deck text)"
    data.pop("slide_order", None)
    return data


# --------------------------------------------------------------------------- #
# Structured output schema
# --------------------------------------------------------------------------- #

# Schema design note: constrained decoding can degenerate into a repetition loop
# when an unbounded array's items carry an `enum`. Two defences are applied here --
# category scores are a fixed object rather than an array, and no property uses an
# `enum`. The allowed values are stated in the prompt and enforced in Python
# (analysis.investor_review and analysis.scoring), which is where they belong anyway,
# since third-party providers apply schemas with differing strictness.

_FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "evidence": {"type": "string"},          # FOUND IN DECK | INFERRED | NOT FOUND
        "slides": {"type": "array", "items": {"type": "integer"}},
        "why_investors_care": {"type": "string"},
    },
    "required": ["text", "evidence", "slides", "why_investors_care"],
    "additionalProperties": False,
}

_CATEGORY_SCORE_SCHEMA = {
    "type": "object",
    "properties": {key: {"type": "number"} for key in WEIGHTS},
    "required": list(WEIGHTS),
    "additionalProperties": False,
}

REVIEW_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "company": {"type": "string"},
        "sector": {"type": ["string", "null"]},
        "stage": {"type": ["string", "null"]},
        "investment_thesis": {"type": "string"},
        "category_scores": _CATEGORY_SCORE_SCHEMA,
        "strengths": {"type": "array", "items": _FINDING_SCHEMA},
        "weaknesses": {"type": "array", "items": _FINDING_SCHEMA},
        "priority_improvements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "priority": {"type": "string"},   # HIGH | MEDIUM | LOW
                    "text": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["priority", "text", "rationale"],
                "additionalProperties": False,
            },
        },
        "presentation_analysis": {"type": "string"},
        "flow_verdict": {"type": "string"},
        "recommended_slide_flow": {"type": "array", "items": {"type": "string"}},
        "not_found": {"type": "array", "items": {"type": "string"}},
        "bottom_line": {"type": "string"},
    },
    "required": [
        "company", "sector", "stage", "investment_thesis", "category_scores",
        "strengths", "weaknesses", "priority_improvements", "presentation_analysis",
        "flow_verdict", "recommended_slide_flow", "not_found", "bottom_line",
    ],
    "additionalProperties": False,
}

"""Swappable LLM backends for the investor analysis.

Every provider takes the extracted deck plus the normalized profile and returns a
raw ``dict`` matching :data:`analysis.prompts.REVIEW_SCHEMA`. Validation, scoring
reconciliation and object construction happen one layer up in
:mod:`analysis.investor_review`, so providers stay thin and interchangeable.

Selection order (unless ``--provider`` forces one):
  1. Anthropic, if ``ANTHROPIC_API_KEY`` is set.
  2. OpenAI, if ``OPENAI_API_KEY`` is set.
  3. The offline heuristic reviewer, which needs no credentials.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from analysis import prompts
from analysis.scoring import WEIGHTS, heuristic_category_scores
from models.schemas import DeckProfile, ExtractedDeck
from utils.validation import AnalysisError

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_OPENAI_MODEL = "gpt-4o"
MAX_TOKENS = 64_000


def is_degenerate(raw: Any) -> bool:
    """True when a model response is unusable rather than merely critical.

    Catches the two observed failure modes of schema-constrained decoding: an
    entirely empty review body, and an array padded with repeated identical entries.
    """
    if not isinstance(raw, dict):
        return True

    lists = ("strengths", "weaknesses", "priority_improvements")
    if all(not raw.get(key) for key in lists):
        return True

    for key in lists:
        items = raw.get(key) or []
        texts = [str(i.get("text", "")) for i in items if isinstance(i, dict)]
        if len(texts) >= 4 and len(set(texts)) <= max(1, len(texts) // 3):
            return True

    scores = raw.get("category_scores")
    if isinstance(scores, list) and len(scores) > len(WEIGHTS) + 2:
        return True
    return False


class LLMProvider(ABC):
    """Interface every analysis backend implements."""

    name: str = "base"
    requires_credentials: bool = True

    @abstractmethod
    def analyze_deck(self, deck: ExtractedDeck, profile: DeckProfile) -> Dict[str, Any]:
        """Return a dict conforming to ``prompts.REVIEW_SCHEMA``."""

    @staticmethod
    def available() -> bool:
        return True


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #

class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: Optional[str] = None) -> None:
        try:
            import anthropic  # type: ignore
        except ImportError as exc:
            raise AnalysisError(
                "The anthropic package is not installed.",
                "Install it with: pip install anthropic",
            ) from exc
        self._anthropic = anthropic
        self.model = model or os.getenv("DECK_REVIEW_MODEL") or DEFAULT_ANTHROPIC_MODEL
        self.client = anthropic.Anthropic()

    @staticmethod
    def available() -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))

    def analyze_deck(self, deck: ExtractedDeck, profile: DeckProfile) -> Dict[str, Any]:
        user_prompt = prompts.build_user_prompt(deck, profile)
        request: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": prompts.SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_prompt}],
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": "high",
                "format": {"type": "json_schema", "schema": prompts.REVIEW_SCHEMA},
            },
        }

        # Schema-constrained decoding occasionally collapses into a repetition loop
        # that fills the response with duplicate or empty entries. One retry clears it.
        raw = None
        for attempt in (1, 2):
            raw = self._one_attempt(request)
            if not is_degenerate(raw):
                return raw
        raise AnalysisError(
            "The model returned an unusable review twice (empty or repeated content).",
            "Retry the run, or use --provider heuristic for an offline review.",
        )

    def _one_attempt(self, request: Dict[str, Any]) -> Dict[str, Any]:
        message = self._send(request)

        if getattr(message, "stop_reason", None) == "refusal":
            details = getattr(message, "stop_details", None)
            raise AnalysisError(
                "The model declined to analyse this deck{}.".format(
                    " (" + str(getattr(details, "category", "")) + ")" if details else ""
                ),
                "Try again, or run with --provider heuristic for an offline review.",
            )

        text = self._first_text(message)
        if not text:
            raise AnalysisError(
                "The model returned no text content.",
                "Retry, or run with --provider heuristic.",
            )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AnalysisError(
                "The model response was not valid JSON: {}".format(exc),
                "Retry the run; this is usually transient.",
            ) from exc

    # -- transport ---------------------------------------------------------- #

    def _send(self, request: Dict[str, Any]):
        """Stream on the beta endpoint with refusal fallbacks; degrade if unsupported."""
        errors: List[str] = []

        beta_request = dict(request)
        beta_request["betas"] = ["server-side-fallback-2026-07-01"]
        beta_request["fallbacks"] = "default"
        try:
            with self.client.beta.messages.stream(**beta_request) as stream:
                return stream.get_final_message()
        except (TypeError, self._anthropic.BadRequestError) as exc:
            # Older SDK or an account without the beta: retry without those extras.
            errors.append(str(exc))
        except self._anthropic.APIStatusError as exc:
            raise self._translate(exc) from exc
        except self._anthropic.APIConnectionError as exc:
            raise AnalysisError(
                "Could not reach the Anthropic API: {}".format(exc),
                "Check your network connection and retry.",
            ) from exc

        try:
            with self.client.messages.stream(**request) as stream:
                return stream.get_final_message()
        except self._anthropic.APIStatusError as exc:
            raise self._translate(exc) from exc
        except self._anthropic.APIConnectionError as exc:
            raise AnalysisError(
                "Could not reach the Anthropic API: {}".format(exc),
                "Check your network connection and retry.",
            ) from exc
        except TypeError as exc:
            raise AnalysisError(
                "This anthropic SDK version rejected the request: {}".format(exc),
                "Upgrade with: pip install --upgrade anthropic. Earlier attempt: {}".format(
                    errors[0] if errors else "n/a"
                ),
            ) from exc

    def _translate(self, exc) -> AnalysisError:
        status = getattr(exc, "status_code", None)
        if status == 401:
            return AnalysisError(
                "Anthropic rejected the API key.",
                "Check ANTHROPIC_API_KEY, or run with --provider heuristic.",
            )
        if status == 429:
            return AnalysisError(
                "Rate limited by the Anthropic API.",
                "Wait a moment and retry.",
            )
        if status == 404:
            return AnalysisError(
                "Model '{}' is not available to this account.".format(self.model),
                "Set DECK_REVIEW_MODEL to a model you have access to.",
            )
        return AnalysisError(
            "Anthropic API error ({}): {}".format(status, exc),
            "Retry, or run with --provider heuristic for an offline review.",
        )

    @staticmethod
    def _first_text(message) -> str:
        for block in getattr(message, "content", []) or []:
            if getattr(block, "type", None) == "text":
                return block.text
        return ""


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #

class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: Optional[str] = None) -> None:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            raise AnalysisError(
                "The openai package is not installed.",
                "Install it with: pip install openai",
            ) from exc
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
        self.client = OpenAI()

    @staticmethod
    def available() -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))

    def analyze_deck(self, deck: ExtractedDeck, profile: DeckProfile) -> Dict[str, Any]:
        user_prompt = prompts.build_user_prompt(deck, profile)
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": prompts.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "investor_presentation_review",
                        "schema": prompts.REVIEW_SCHEMA,
                        "strict": True,
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001 - SDK raises many types
            raise AnalysisError(
                "OpenAI API error: {}".format(exc),
                "Retry, or run with --provider heuristic for an offline review.",
            ) from exc

        content = response.choices[0].message.content or ""
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise AnalysisError(
                "The model response was not valid JSON: {}".format(exc)
            ) from exc


# --------------------------------------------------------------------------- #
# Offline heuristic reviewer
# --------------------------------------------------------------------------- #

class HeuristicProvider(LLMProvider):
    """Deterministic, credential-free review built from parser signals.

    Deliberately conservative: it reports structural presence/absence and density,
    which is all a rule-based pass can honestly assess. It never characterises the
    quality of an argument it cannot read.
    """

    name = "heuristic"
    requires_credentials = False

    def analyze_deck(self, deck: ExtractedDeck, profile: DeckProfile) -> Dict[str, Any]:
        categories = heuristic_category_scores(profile)
        sections = profile.detected_sections
        design = profile.design_signals or {}

        strengths = self._strengths(profile, sections)
        weaknesses = self._weaknesses(profile, sections, design)
        improvements = self._improvements(profile, sections, design)
        not_found = list(profile.missing_sections)
        if not profile.fundraising.raise_amount:
            not_found.append("Fundraising ask (amount)")
        if not profile.fundraising.use_of_funds:
            not_found.append("Use of funds breakdown")
        if not profile.market.tam:
            not_found.append("Market sizing (TAM)")

        from analysis.scoring import flow_is_ordered, total_score

        ordered, inversions = flow_is_ordered(profile.slide_order)
        score = total_score(categories)

        return {
            "company": profile.company_name or "Unknown Company",
            "sector": profile.sector,
            "stage": profile.stage,
            "investment_thesis": self._thesis(profile),
            "category_scores": [
                {"category": c.category, "score": c.score, "note": c.note}
                for c in categories
            ],
            "strengths": strengths,
            "weaknesses": weaknesses,
            "priority_improvements": improvements,
            "presentation_analysis": (
                "Structural review only (no language model was used). The deck runs {} "
                "slides at an average of {:.0f} words per slide, with {} dense slide(s) "
                "and {} image-dominant slide(s). Narrative sequence has {} out-of-order "
                "transition(s) against the standard investor flow."
            ).format(
                profile.slide_count,
                float(design.get("avg_words_per_slide") or 0),
                len(design.get("very_dense_slides") or []),
                len(design.get("image_only_slides") or []),
                inversions,
            ),
            "flow_verdict": (
                "Current flow follows the standard investor sequence."
                if ordered else
                "Narrative moves backwards {} time(s) against the standard sequence.".format(
                    inversions
                )
            ),
            "recommended_slide_flow": [] if ordered else [
                "Investment thesis", "Problem", "Solution", "Product", "Why now",
                "Market", "Traction", "Business model", "Go-to-market", "Competition",
                "Team", "The ask and use of funds",
            ],
            "not_found": not_found[:10],
            "bottom_line": (
                "Structural score of {}/100 based on which investor-critical sections are "
                "present and how densely they are presented. {} Run again with an API key "
                "configured for a substantive read of the argument itself."
            ).format(
                score,
                "The main gaps are: {}.".format(", ".join(not_found[:3]))
                if not_found else "No major sections are missing.",
            ),
        }

    # -- component builders -------------------------------------------------- #

    def _thesis(self, profile: DeckProfile) -> str:
        bits = []
        if profile.company_name:
            bits.append(profile.company_name)
        if profile.sector:
            bits.append("operates in {}".format(profile.sector))
        if profile.stage:
            bits.append("and presents at {} stage".format(profile.stage.lower()))
        if not bits:
            return ("The deck does not state a clear investment thesis that could be "
                    "extracted mechanically.")
        return (
            "{}. This summary is structural only -- no language model was used, so the "
            "strength of the underlying thesis has not been assessed."
        ).format(" ".join(bits))

    def _strengths(self, profile: DeckProfile, sections) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if profile.traction:
            out.append(self._f(
                "Traction is quantified: {}".format("; ".join(profile.traction[:2])),
                sections.get("traction", []),
                "Numbers convert a story into evidence.",
            ))
        if profile.market.methodology == "bottom-up":
            out.append(self._f(
                "Market sizing uses a bottom-up build rather than a top-down claim.",
                sections.get("market", []),
                "Bottom-up sizing survives diligence; top-down rarely does.",
            ))
        if profile.fundraising.raise_amount and profile.fundraising.use_of_funds:
            out.append(self._f(
                "The ask ({}) is paired with a use-of-funds breakdown.".format(
                    profile.fundraising.raise_amount),
                sections.get("fundraising", []),
                "Investors fund milestones, not round sizes.",
            ))
        if len(profile.team) >= 3:
            out.append(self._f(
                "Leadership is named with roles: {}.".format("; ".join(profile.team[:3])),
                sections.get("team", []),
                "Named, credentialled operators reduce execution risk.",
            ))
        if profile.competition:
            out.append(self._f(
                "Competitors are named directly ({}).".format(
                    ", ".join(profile.competition[:4])),
                sections.get("competition", []),
                "Naming competitors signals market awareness.",
            ))
        return out[:5]

    def _weaknesses(self, profile: DeckProfile, sections, design) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for label in profile.missing_sections[:4]:
            out.append({
                "text": "No dedicated {} slide was found.".format(label.lower()),
                "evidence": "NOT FOUND",
                "slides": [],
                "why_investors_care": "Investors screen for this section explicitly.",
            })
        if profile.market.tam and not profile.market.sam:
            out.append(self._f(
                "Market sizing presents a headline TAM ({}) without a serviceable "
                "segment.".format(profile.market.tam),
                sections.get("market", []),
                "A TAM with no SAM reads as top-down and invites scepticism.",
            ))
        no_comp = design.get("no_competitor_claim_slides") or []
        if no_comp:
            out.append(self._f(
                "The deck claims to have no direct competitors.",
                list(no_comp[:3]),
                "Investors read this as either a small market or incomplete research.",
            ))
        very_dense = design.get("very_dense_slides") or []
        if very_dense:
            out.append(self._f(
                "Slide(s) {} carry over 170 words each.".format(
                    ", ".join(str(s) for s in very_dense[:5])),
                list(very_dense[:5]),
                "Dense slides are skimmed, so the key point is missed.",
            ))
        if not profile.fundraising.raise_amount:
            out.append({
                "text": "The deck does not state how much the company is raising.",
                "evidence": "NOT FOUND",
                "slides": [],
                "why_investors_care": "Without an ask there is nothing to evaluate.",
            })
        for key, label, why in (
            ("defensibility", "defensibility or IP",
             "Without a moat, early traction invites fast followers."),
            ("risks", "risk and mitigation",
             "Founders who name their own risks are trusted more, not less."),
            ("why_now", "why now",
             "Timing is what separates a good idea from a fundable one."),
        ):
            if not sections.get(key):
                out.append({
                    "text": "No {} slide was found.".format(label),
                    "evidence": "NOT FOUND",
                    "slides": [],
                    "why_investors_care": why,
                })
        image_only = design.get("image_only_slides") or []
        if len(image_only) >= 3:
            out.append({
                "text": "{} slide(s) contain images with almost no extractable text, "
                        "so their content could not be assessed.".format(len(image_only)),
                "evidence": "NOT FOUND",
                "slides": list(image_only[:4]),
                "why_investors_care": "Text in images is invisible to screening tools "
                                      "and to accessibility software.",
            })
        return out[:6]

    def _improvements(self, profile: DeckProfile, sections, design) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for label in profile.missing_sections[:3]:
            out.append({
                "priority": "HIGH",
                "text": "Add a dedicated {} slide.".format(label.lower()),
                "rationale": "Investors look for this section by name when screening.",
            })
        if not profile.fundraising.raise_amount:
            out.append({
                "priority": "HIGH",
                "text": "State the raise amount, instrument and valuation explicitly.",
                "rationale": "The ask is the point of the deck.",
            })
        if not profile.fundraising.use_of_funds:
            out.append({
                "priority": "HIGH",
                "text": "Add a use-of-funds breakdown tied to the milestones the round buys.",
                "rationale": "Connects capital to value creation.",
            })
        if profile.market.methodology == "top-down":
            out.append({
                "priority": "HIGH",
                "text": "Replace the {} top-down market figure with a bottom-up build: "
                        "target accounts x annual contract value.".format(
                            profile.market.tam or "headline"),
                "rationale": "Bottom-up sizing is the one investors trust.",
            })
        if design.get("no_competitor_claim_slides"):
            out.append({
                "priority": "HIGH",
                "text": "Remove the 'no direct competitors' claim and replace it with a "
                        "positioning matrix naming the alternatives buyers use today.",
                "rationale": "The claim reads as incomplete research, not as a moat.",
            })
        if not sections.get("risks"):
            out.append({
                "priority": "MEDIUM",
                "text": "Add a short risk and mitigation slide covering the top technical, "
                        "commercial and financing risks.",
                "rationale": "Naming your own risks builds credibility in diligence.",
            })
        for slide in (design.get("very_dense_slides") or [])[:2]:
            out.append({
                "priority": "MEDIUM",
                "text": "Cut slide {} to a single headline claim plus one supporting "
                        "chart.".format(slide),
                "rationale": "Over 200 words on a slide guarantees it is skimmed.",
            })
        for slide in (design.get("slides_without_titles") or [])[:2]:
            out.append({
                "priority": "LOW",
                "text": "Give slide {} an assertive headline that states the takeaway.".format(slide),
                "rationale": "Headlines carry the argument when a deck is read without you.",
            })
        if profile.slide_count > 25:
            out.append({
                "priority": "LOW",
                "text": "Move detail beyond slide 20 into an appendix.",
                "rationale": "A first-meeting deck should read in ten minutes.",
            })
        return out[:8]

    @staticmethod
    def _f(text: str, slides, why: str) -> Dict[str, Any]:
        return {
            "text": text,
            "evidence": "FOUND IN DECK",
            "slides": list(slides)[:4],
            "why_investors_care": why,
        }


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #

PROVIDERS = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "heuristic": HeuristicProvider,
}


def get_provider(name: Optional[str] = None, model: Optional[str] = None) -> LLMProvider:
    """Resolve a provider by name, or auto-select based on available credentials."""
    if name:
        key = name.lower()
        if key not in PROVIDERS:
            raise AnalysisError(
                "Unknown provider '{}'.".format(name),
                "Choose one of: {}.".format(", ".join(PROVIDERS)),
            )
        cls = PROVIDERS[key]
        if cls.requires_credentials and not cls.available():
            raise AnalysisError(
                "Provider '{}' has no API key configured.".format(key),
                "Set the relevant API key, or use --provider heuristic.",
            )
        return cls(model) if cls is not HeuristicProvider else cls()

    for key in ("anthropic", "openai"):
        cls = PROVIDERS[key]
        if cls.available():
            try:
                return cls(model)
            except AnalysisError:
                continue
    return HeuristicProvider()

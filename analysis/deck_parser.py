"""Turn an :class:`ExtractedDeck` into a normalized :class:`DeckProfile`.

This layer is deliberately deterministic and conservative: it only records what it
can point at in the deck. Anything it cannot find stays ``None`` so that downstream
analysis can say NOT FOUND honestly rather than inventing a value.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from models.schemas import (
    DeckProfile,
    ExtractedDeck,
    Financials,
    Fundraising,
    Market,
)

# --------------------------------------------------------------------------- #
# Section vocabulary -- keyword -> canonical section
# --------------------------------------------------------------------------- #

SECTION_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "thesis": ("investment thesis", "why invest", "executive summary", "overview",
               "company overview", "at a glance", "snapshot"),
    "problem": ("problem", "pain point", "the challenge", "challenges", "status quo",
                "unmet need", "why it matters", "the gap"),
    "solution": ("solution", "our approach", "how it works", "the answer",
                 "what we do", "our platform", "technology"),
    "product": ("product", "platform", "demo", "features", "how it works",
                "product roadmap", "screenshots"),
    "why_now": ("why now", "timing", "market timing", "inflection", "tailwind"),
    "market": ("market", "tam", "sam", "som", "market size", "opportunity",
               "market opportunity", "addressable market", "industry"),
    "traction": ("traction", "milestones achieved", "progress", "momentum",
                 "customers", "pilots", "case study", "results", "validation",
                 "growth", "kpis", "metrics"),
    "business_model": ("business model", "revenue model", "pricing", "how we make money",
                       "monetization", "unit economics", "subscription"),
    "gtm": ("go-to-market", "go to market", "gtm", "sales", "distribution",
            "customer acquisition", "channels", "marketing", "sales strategy"),
    "competition": ("competition", "competitive", "competitors", "landscape",
                    "alternatives", "vs.", "comparison", "market map"),
    "defensibility": ("defensibility", "moat", "intellectual property", "ip",
                      "patent", "patents", "barriers to entry", "proprietary"),
    "financials": ("financials", "financial", "projections", "forecast", "p&l",
                   "revenue projections", "burn", "runway", "cash flow", "model"),
    "team": ("team", "founders", "leadership", "who we are", "advisors",
             "management", "our people"),
    "fundraising": ("the ask", "raise", "fundraising", "funding", "investment ask",
                    "we are raising", "round", "terms", "safe", "convertible"),
    "use_of_funds": ("use of funds", "use of proceeds", "allocation", "spend plan"),
    "milestones": ("milestones", "roadmap", "timeline", "plan", "next 18 months",
                   "next steps", "what's next"),
    "risks": ("risk", "risks", "mitigation", "risk factors"),
    "vision": ("vision", "mission", "closing", "thank you", "contact", "appendix"),
}

# The canonical investor narrative used to judge ordering (spec section 10).
CANONICAL_FLOW = [
    "thesis", "problem", "solution", "product", "why_now", "market", "traction",
    "business_model", "gtm", "competition", "defensibility", "financials", "team",
    "fundraising", "use_of_funds", "vision",
]

# Sections an investor expects to find; used for the NOT FOUND list.
EXPECTED_SECTIONS = [
    "problem", "solution", "market", "traction", "business_model", "gtm",
    "competition", "team", "financials", "fundraising",
]

SECTION_LABELS = {
    "thesis": "Investment thesis / summary",
    "problem": "Problem",
    "solution": "Solution",
    "product": "Product",
    "why_now": "Why now",
    "market": "Market opportunity",
    "traction": "Traction",
    "business_model": "Business model",
    "gtm": "Go-to-market",
    "competition": "Competition",
    "defensibility": "Defensibility / IP",
    "financials": "Financials",
    "team": "Team",
    "fundraising": "Fundraising ask",
    "use_of_funds": "Use of funds",
    "milestones": "Milestones",
    "risks": "Risks",
    "vision": "Vision / closing",
}

# --------------------------------------------------------------------------- #
# Regex library
# --------------------------------------------------------------------------- #

MONEY = r"(?:[$€£]\s?\d[\d,]*(?:\.\d+)?\s?(?:billion|million|thousand|trillion|bn|mm|[BMKT])?)"
MONEY_RE = re.compile(MONEY, re.IGNORECASE)
PERCENT_RE = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%")

STAGE_RE = re.compile(
    r"\b(pre-seed|preseed|seed(?:\s?\+)?|series\s?[a-e]\b|bridge\s?round|"
    r"friends\s?(?:and|&)\s?family|angel\s?round)\b",
    re.IGNORECASE,
)
SECURITY_RE = re.compile(
    r"\b(safe|simple agreement for future equity|convertible note|convertible debt|"
    r"priced (?:equity )?round|preferred (?:stock|shares|equity)|common (?:stock|equity)|"
    r"kiss|equity round)\b",
    re.IGNORECASE,
)
RUNWAY_RE = re.compile(r"\b(\d{1,2})\s?(?:-|to)?\s?(\d{1,2})?\s?months?\s+(?:of\s+)?runway", re.IGNORECASE)
BURN_RE = re.compile(r"(?:monthly\s+)?burn(?:\s+rate)?[^.\n]{0,40}?(" + MONEY + r")", re.IGNORECASE)
MARGIN_RE = re.compile(r"gross\s+margins?[^.\n]{0,30}?(\d{1,3}(?:\.\d+)?\s?%)", re.IGNORECASE)
ARR_RE = re.compile(r"\b(ARR|MRR|annual recurring revenue|monthly recurring revenue)\b[^.\n]{0,40}?(" + MONEY + r")", re.IGNORECASE)
REVENUE_RE = re.compile(r"\brevenue\b[^.\n]{0,40}?(" + MONEY + r")", re.IGNORECASE)
RAISE_RE = re.compile(
    r"(?:raising|raise|seeking|the ask|ask:|round size|target raise)[^.\n]{0,40}?(" + MONEY + r")",
    re.IGNORECASE,
)
VALUATION_RE = re.compile(
    r"(?:valuation|pre-money|post-money|cap\b|valuation cap)[^.\n]{0,30}?(" + MONEY + r")",
    re.IGNORECASE,
)
COMMITTED_RE = re.compile(
    r"(?:committed|soft[- ]circled|closed|secured|subscribed)[^.\n]{0,40}?(" + MONEY + r")",
    re.IGNORECASE,
)
NO_COMPETITORS_RE = re.compile(
    r"\b(no (?:direct )?competit\w+|there are no competit\w+|first(?:-| )of(?:-| )its(?:-| )kind|"
    r"we have no competition|nothing like (?:this|it) exists)\b",
    re.IGNORECASE,
)
BOTTOM_UP_RE = re.compile(
    r"\b(bottom[- ]up|bottoms[- ]up|\d[\d,]*\s+(?:hospitals|customers|accounts|clinics|"
    r"practices|users|sites|facilities)\s*[x×*]\s*|per[- ]customer|acv|contract value)\b",
    re.IGNORECASE,
)
TOP_DOWN_RE = re.compile(r"\b(top[- ]down|total market|global market size|industry is worth)\b", re.IGNORECASE)

TITLE_ROLE_RE = re.compile(
    r"\b(ceo|cto|coo|cfo|cso|cmo|chief\s+\w+\s+officer|founder|co-?founder|"
    r"president|vp\s+\w+|head of \w+|chair(?:man|woman|person)?|advisor|board member|"
    r"managing director|partner|principal investigator)\b",
    re.IGNORECASE,
)
PERSON_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z]+(?:-[A-Z][a-z]+)?)\b")

GENERIC_TITLES = {
    "powerpoint presentation", "presentation", "untitled", "slide 1", "deck",
    "pitch deck", "confidential", "microsoft powerpoint",
}

STOPWORD_TOKENS = {
    "The", "This", "That", "Our", "We", "Your", "Their", "And", "For", "With",
    "How", "Why", "What", "When", "Who", "Confidential", "Slide", "Page",
    "Problem", "Solution", "Market", "Team", "Traction", "Product", "Company",
    "Overview", "Summary", "Vision", "Mission", "Contact", "Thank", "You",
    "Appendix", "Introduction", "Executive", "Investment",
}


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def parse_deck(deck: ExtractedDeck) -> DeckProfile:
    """Build the normalized profile. Nothing is invented -- absent stays absent."""
    profile = DeckProfile(slide_count=deck.slide_count)
    text = deck.full_text
    lower = text.lower()

    profile.detected_sections = _detect_sections(deck)
    profile.slide_order = _slide_order(deck, profile.detected_sections)
    profile.missing_sections = [
        SECTION_LABELS[s] for s in EXPECTED_SECTIONS
        if not profile.detected_sections.get(s)
    ]

    profile.company_name = _company_name(deck)
    profile.stage = _first_match(STAGE_RE, text)
    profile.sector = _sector(lower)
    profile.location = _location(text)

    profile.problem = _section_summary(deck, profile.detected_sections, "problem")
    profile.solution = _section_summary(deck, profile.detected_sections, "solution")
    profile.product = _section_summary(deck, profile.detected_sections, "product")
    profile.business_model = _section_summary(deck, profile.detected_sections, "business_model")
    profile.revenue_model = _revenue_model(lower)
    profile.go_to_market = _section_summary(deck, profile.detected_sections, "gtm")
    profile.competitive_advantage = _section_summary(deck, profile.detected_sections, "defensibility")

    profile.market = _market(deck, text)
    profile.financials = _financials(text)
    profile.fundraising = _fundraising(deck, text)

    profile.traction = _traction(deck)
    profile.customers = _customers(deck)
    profile.growth_metrics = _growth_metrics(text)
    profile.competition = _competition(deck)
    profile.team = _team(deck)
    profile.milestones = _bullets_from_sections(deck, profile.detected_sections,
                                                ("milestones", "use_of_funds"), limit=6)
    profile.risks = _bullets_from_sections(deck, profile.detected_sections, ("risks",), limit=6)

    profile.design_signals = _design_signals(deck)
    return profile


# --------------------------------------------------------------------------- #
# Section detection
# --------------------------------------------------------------------------- #

def _detect_sections(deck: ExtractedDeck) -> Dict[str, List[int]]:
    """Map section -> slide indices.

    Title matches win over body matches, and among title matches the most specific
    keyword wins: "Go-To-Market" is a GTM slide, not a Market slide, because
    ``go-to-market`` is a longer match than ``market``. A near-tie keeps both, so
    "Problem & Solution" is still tagged as both.
    """
    found: Dict[str, List[int]] = {}

    for slide in deck.slides:
        title = slide.title.lower()
        body = slide.combined_text.lower()

        title_hits: Dict[str, int] = {}
        body_hits: List[str] = []

        for section, keywords in SECTION_KEYWORDS.items():
            best = 0
            for keyword in keywords:
                if keyword in title:
                    best = max(best, len(keyword))
            if best:
                title_hits[section] = best
                continue
            # Body matches need the keyword to sit at the start of a line, so we do
            # not tag every slide that happens to say "market".
            for keyword in keywords:
                if len(keyword) > 6 and re.search(
                    r"(?:^|\n)\s*" + re.escape(keyword), body
                ):
                    body_hits.append(section)
                    break

        if title_hits:
            strongest = max(title_hits.values())
            sections = [s for s, n in title_hits.items() if n >= strongest - 2]
        else:
            sections = []
        sections.extend(s for s in body_hits if s not in sections)

        for section in sections:
            found.setdefault(section, []).append(slide.index)

    return found


def _slide_order(deck: ExtractedDeck, sections: Dict[str, List[int]]) -> List[str]:
    """Best-guess section label per slide, in presentation order."""
    per_slide: Dict[int, str] = {}
    for section, indices in sections.items():
        for index in indices:
            # First writer wins unless the new section is earlier in the canonical flow.
            current = per_slide.get(index)
            if current is None or _flow_rank(section) < _flow_rank(current):
                per_slide[index] = section
    return [per_slide.get(s.index, "other") for s in deck.slides]


def _flow_rank(section: str) -> int:
    return CANONICAL_FLOW.index(section) if section in CANONICAL_FLOW else 99


# --------------------------------------------------------------------------- #
# Field extraction
# --------------------------------------------------------------------------- #

def _company_name(deck: ExtractedDeck) -> Optional[str]:
    """Metadata title, then a repeated brand token, then slide 1, then the filename."""
    meta_title = (deck.metadata.get("title") or "").strip()
    if meta_title and meta_title.lower() not in GENERIC_TITLES and len(meta_title) < 60:
        return meta_title

    # A brand name usually repeats in footers across many slides.
    counter: Counter = Counter()
    for slide in deck.slides:
        tokens = set(
            re.findall(r"\b([A-Z][A-Za-z0-9&.\-]{2,24})\b", slide.combined_text)
        )
        for token in tokens:
            if token in STOPWORD_TOKENS or token.isupper() and len(token) <= 3:
                continue
            counter[token] += 1
    if counter:
        token, hits = counter.most_common(1)[0]
        if hits >= max(3, deck.slide_count // 2):
            return token

    if deck.slides:
        title = deck.slides[0].title.strip()
        if title and title.lower() not in GENERIC_TITLES and len(title) < 60:
            return title.split("\n")[0]
        first_line = (deck.slides[0].text or "").strip().split("\n")
        if first_line and 1 < len(first_line[0]) < 60:
            return first_line[0]

    stem = Path(deck.source_path).stem
    stem = re.sub(r"(?i)[_\-\s]*(pitch|deck|investor|presentation|final|v\d+|20\d\d).*$", "", stem)
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    return stem.title() if stem else None


SECTOR_HINTS = {
    "healthcare / biotech": ("clinical", "patients", "fda", "therapeutic", "biotech",
                             "drug", "diagnos", "hospital", "medical", "pharma"),
    "fintech": ("payments", "lending", "banking", "fintech", "underwriting", "credit",
                "insurance", "wallet"),
    "enterprise software / SaaS": ("saas", "enterprise software", "workflow", "b2b",
                                   "platform for teams", "crm", "erp"),
    "artificial intelligence": ("machine learning", "artificial intelligence", "llm",
                                "neural", "ai model", "foundation model"),
    "climate / energy": ("carbon", "emissions", "renewable", "solar", "battery",
                         "climate", "grid", "decarbon"),
    "consumer": ("consumer", "marketplace", "d2c", "shoppers", "subscribers", "app users"),
    "industrial / hardware": ("manufacturing", "robotics", "hardware", "supply chain",
                              "logistics", "sensor"),
}


def _sector(lower_text: str) -> Optional[str]:
    scores = {
        sector: sum(lower_text.count(hint) for hint in hints)
        for sector, hints in SECTOR_HINTS.items()
    }
    best = max(scores, key=lambda s: scores[s]) if scores else None
    return best if best and scores[best] >= 3 else None


def _location(text: str) -> Optional[str]:
    match = re.search(
        r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?),\s?(?:[A-Z]{2}\b|[A-Z][a-z]+\b)", text
    )
    return match.group(0) if match else None


def _market(deck: ExtractedDeck, text: str) -> Market:
    market = Market()
    market.tam = _labelled_money(text, ("tam", "total addressable market", "total market"))
    market.sam = _labelled_money(text, ("sam", "serviceable addressable market",
                                        "serviceable available market"))
    market.som = _labelled_money(text, ("som", "serviceable obtainable market",
                                        "obtainable market", "beachhead"))

    market_text = _sections_text(deck, ("market",)) or text
    if BOTTOM_UP_RE.search(market_text):
        market.methodology = "bottom-up"
    elif TOP_DOWN_RE.search(market_text) or (market.tam and not market.sam):
        market.methodology = "top-down"
    return market


def _labelled_money(text: str, labels: Tuple[str, ...]) -> Optional[str]:
    """Find a currency figure near one of the given labels, in either order."""
    for label in labels:
        pattern = re.compile(
            r"(?:" + re.escape(label) + r"\b[^.\n]{0,40}?(" + MONEY + r")"
            r"|(" + MONEY + r")[^.\n]{0,25}?\b" + re.escape(label) + r"\b)",
            re.IGNORECASE,
        )
        match = pattern.search(text)
        if match:
            return (match.group(1) or match.group(2)).strip()
    return None


def _financials(text: str) -> Financials:
    financials = Financials()
    arr = ARR_RE.search(text)
    if arr:
        financials.revenue = "{} {}".format(arr.group(1).upper(), arr.group(2).strip())
    else:
        rev = REVENUE_RE.search(text)
        if rev:
            financials.revenue = rev.group(1).strip()

    margin = MARGIN_RE.search(text)
    if margin:
        financials.gross_margin = margin.group(1).strip()

    burn = BURN_RE.search(text)
    if burn:
        financials.burn = burn.group(1).strip()

    runway = RUNWAY_RE.search(text)
    if runway:
        financials.runway = runway.group(0).strip()

    if re.search(r"\b(projections?|forecast|5[- ]year|three[- ]year|20\d\d\s?[-–]\s?20\d\d)\b",
                 text, re.IGNORECASE):
        financials.projections = "Financial projections referenced in the deck"
    return financials


def _fundraising(deck: ExtractedDeck, text: str) -> Fundraising:
    fundraising = Fundraising()
    raise_match = RAISE_RE.search(text)
    if raise_match:
        fundraising.raise_amount = raise_match.group(1).strip()

    valuation = VALUATION_RE.search(text)
    if valuation:
        fundraising.valuation = valuation.group(1).strip()

    security = SECURITY_RE.search(text)
    if security:
        fundraising.security = security.group(0).strip()

    committed = COMMITTED_RE.search(text)
    if committed:
        fundraising.committed = committed.group(1).strip()

    use_text = _sections_text(deck, ("use_of_funds",))
    if use_text:
        fundraising.use_of_funds = _bullets(use_text, limit=6)
    return fundraising


def _revenue_model(lower_text: str) -> Optional[str]:
    models = [
        ("subscription / SaaS", ("subscription", "saas", "per seat", "per user",
                                 "annual contract", "recurring revenue")),
        ("transactional / take rate", ("take rate", "per transaction", "commission",
                                       "transaction fee")),
        ("licensing / royalty", ("license fee", "licensing", "royalty", "milestone payments")),
        ("usage-based", ("usage-based", "per api call", "consumption", "pay as you go")),
        ("hardware / product sales", ("unit price", "per device", "hardware sales",
                                      "gross profit per unit")),
        ("services", ("consulting", "professional services", "service contract")),
    ]
    for label, hints in models:
        if sum(lower_text.count(h) for h in hints) >= 2:
            return label
    return None


def _traction(deck: ExtractedDeck) -> List[str]:
    """Lines from traction-ish slides that carry a number -- traction needs evidence."""
    out: List[str] = []
    text = _sections_text(deck, ("traction", "financials", "thesis"))
    for line in _lines(text):
        if len(line) < 8 or len(line) > 160:
            continue
        if MONEY_RE.search(line) or PERCENT_RE.search(line) or re.search(r"\b\d{2,}\b", line):
            if re.search(r"\b(customer|user|revenue|arr|mrr|pilot|contract|loi|partner|"
                         r"growth|retention|churn|patients|sites|units|bookings|waitlist|"
                         r"downloads|grant|award|clinical|fda|patent)\b", line, re.IGNORECASE):
                out.append(line)
    return _unique(out)[:8]


def _customers(deck: ExtractedDeck) -> List[str]:
    text = _sections_text(deck, ("traction", "gtm"))
    names: List[str] = []
    for line in _lines(text):
        if re.search(r"\b(customers?|clients?|partners?|pilots?|deployed at|used by)\b",
                     line, re.IGNORECASE) and len(line) < 160:
            names.append(line)
    return _unique(names)[:6]


def _growth_metrics(text: str) -> List[str]:
    out = []
    for match in re.finditer(
        r"[^.\n]{0,60}\b(\d{1,3}(?:\.\d+)?\s?%|[\d.]+x)\s*(?:\w+\s+){0,3}"
        r"(growth|increase|mom|yoy|cagr|retention|margin|conversion|churn)[^.\n]{0,40}",
        text, re.IGNORECASE,
    ):
        line = match.group(0).strip()
        if 10 < len(line) < 140:
            out.append(line)
    return _unique(out)[:6]


def _competition(deck: ExtractedDeck) -> List[str]:
    text = _sections_text(deck, ("competition",))
    if not text:
        return []
    names: List[str] = []
    for line in _lines(text):
        if len(line) > 90:
            continue
        for token in re.findall(r"\b([A-Z][A-Za-z0-9&.\-]{2,20})\b", line):
            if token not in STOPWORD_TOKENS and token.lower() not in {
                "competition", "competitive", "competitors", "landscape", "alternatives",
                "feature", "features", "us", "our", "yes", "no",
            }:
                names.append(token)
    return _unique(names)[:10]


def _team(deck: ExtractedDeck) -> List[str]:
    """Name + role pairs from team slides."""
    text = _sections_text(deck, ("team",))
    if not text:
        return []
    members: List[str] = []
    lines = _lines(text)
    for index, line in enumerate(lines):
        role = TITLE_ROLE_RE.search(line)
        if not role:
            continue
        person = PERSON_RE.search(line)
        if not person and index > 0:
            person = PERSON_RE.search(lines[index - 1])
        if person:
            members.append("{} - {}".format(person.group(1), role.group(0)))
        elif len(line) < 90:
            members.append(line)
    return _unique(members)[:8]


def _bullets_from_sections(deck: ExtractedDeck, sections: Dict[str, List[int]],
                           keys: Tuple[str, ...], limit: int = 6) -> List[str]:
    return _bullets(_sections_text(deck, keys), limit=limit)


# --------------------------------------------------------------------------- #
# Design signals for the visual review (spec section 6)
# --------------------------------------------------------------------------- #

def _design_signals(deck: ExtractedDeck) -> Dict[str, object]:
    word_counts = [s.word_count for s in deck.slides]
    dense = [s.index for s in deck.slides if s.word_count > 100]
    very_dense = [s.index for s in deck.slides if s.word_count > 170]
    font_stats = deck.metadata.get("font_stats") or []
    small_font_slides = [
        i + 1 for i, stat in enumerate(font_stats)
        if isinstance(stat, dict) and stat.get("small_spans", 0) >= 8
    ]
    no_title = [s.index for s in deck.slides if not s.title and s.word_count > 15]

    no_competitor_claim = [
        s.index for s in deck.slides if NO_COMPETITORS_RE.search(s.combined_text)
    ]

    return {
        "no_competitor_claim_slides": no_competitor_claim[:5],
        "slide_count": deck.slide_count,
        "avg_words_per_slide": round(sum(word_counts) / len(word_counts), 1) if word_counts else 0,
        "max_words_on_a_slide": max(word_counts) if word_counts else 0,
        "dense_slides": dense[:10],
        "very_dense_slides": very_dense[:10],
        "image_only_slides": deck.image_only_slides[:10],
        "small_font_slides": small_font_slides[:10],
        "slides_without_titles": no_title[:10],
        "slides_with_tables": [s.index for s in deck.slides if s.tables][:10],
        "total_words": deck.total_words,
    }


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _sections_text(deck: ExtractedDeck, keys: Tuple[str, ...]) -> str:
    sections = _detect_sections(deck)
    indices: List[int] = []
    for key in keys:
        indices.extend(sections.get(key, []))
    if not indices:
        return ""
    wanted = set(indices)
    return "\n".join(s.combined_text for s in deck.slides if s.index in wanted)


def _section_summary(deck: ExtractedDeck, sections: Dict[str, List[int]],
                     key: str, max_chars: int = 400) -> Optional[str]:
    indices = sections.get(key)
    if not indices:
        return None
    wanted = set(indices)
    chunks = [s.combined_text for s in deck.slides if s.index in wanted]
    text = " ".join(" ".join(c.split()) for c in chunks).strip()
    if not text:
        return None
    return text[:max_chars]


def _lines(text: str) -> List[str]:
    return [line.strip(" -•\t") for line in (text or "").splitlines() if line.strip()]


_ALL_KEYWORDS = {k for keywords in SECTION_KEYWORDS.values() for k in keywords}


def _bullets(text: str, limit: int = 6) -> List[str]:
    """Content lines only -- a line that is just the slide heading is not a bullet."""
    out = [
        line for line in _lines(text)
        if 8 < len(line) < 160 and line.lower().strip(": ") not in _ALL_KEYWORDS
    ]
    return _unique(out)[:limit]


def _first_match(pattern: re.Pattern, text: str) -> Optional[str]:
    match = pattern.search(text)
    return match.group(0).strip() if match else None


def _unique(items: List[str]) -> List[str]:
    seen, out = set(), []
    for item in items:
        key = re.sub(r"\s+", " ", item.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out

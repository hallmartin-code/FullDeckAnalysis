"""Test suite for the pitch deck review pipeline.

Runs without any API key -- the LLM layer is exercised through the offline
heuristic provider and through stub providers, so the suite is deterministic.

    python -m pytest tests/ -q
    python tests/test_pipeline.py          # also works without pytest installed
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from analysis.deck_parser import parse_deck  # noqa: E402
from analysis.investor_review import review_deck  # noqa: E402
from analysis.llm_provider import (  # noqa: E402
    HeuristicProvider,
    LLMProvider,
    get_provider,
    is_degenerate,
)
from analysis.scoring import WEIGHTS, heuristic_category_scores, reconcile, total_score  # noqa: E402
from extractors import extract_deck  # noqa: E402
from models.schemas import (  # noqa: E402
    CategoryScore,
    ExtractedDeck,
    Finding,
    Recommendation,
    ReviewResult,
    Slide,
    slugify,
)
from reports.pdf_generator import _shorten, generate_pdf  # noqa: E402
from utils.validation import (  # noqa: E402
    CorruptFileError,
    EmptyDeckError,
    FileAccessError,
    UnsupportedFileTypeError,
    sanitize_text,
    validate_input_file,
    validate_review,
)

sys.path.insert(0, str(ROOT / "tests"))
from make_sample_deck import make_pdf, make_pptx  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session")
def sample_pptx(tmp_path_factory):
    return make_pptx(tmp_path_factory.mktemp("decks") / "sample.pptx")


@pytest.fixture(scope="session")
def sample_pdf(tmp_path_factory):
    return make_pdf(tmp_path_factory.mktemp("decks") / "sample.pdf")


@pytest.fixture(scope="session")
def pptx_deck(sample_pptx):
    return extract_deck(str(sample_pptx))


@pytest.fixture(scope="session")
def pdf_deck(sample_pdf):
    return extract_deck(str(sample_pdf))


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #

def test_pptx_extraction_reads_every_slide(pptx_deck):
    assert pptx_deck.file_type == "pptx"
    assert pptx_deck.slide_count == 14
    assert pptx_deck.has_usable_text
    assert [s.index for s in pptx_deck.slides] == list(range(1, 15))
    assert pptx_deck.slides[1].title == "The Problem"


def test_pdf_extraction_reads_every_page(pdf_deck):
    assert pdf_deck.file_type == "pdf"
    assert pdf_deck.slide_count == 14
    assert "readmissions" in pdf_deck.full_text.lower()


def test_both_formats_recover_the_same_key_facts(pptx_deck, pdf_deck):
    for deck in (pptx_deck, pdf_deck):
        text = deck.full_text
        assert "$1.4M" in text
        assert "$6M" in text


def test_file_type_detected_from_content_not_extension(tmp_path, sample_pdf):
    """A PDF renamed .pptx must still be read as a PDF."""
    disguised = tmp_path / "renamed.pptx"
    disguised.write_bytes(Path(sample_pdf).read_bytes())
    deck = extract_deck(str(disguised))
    assert deck.file_type == "pdf"


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #

def test_missing_file_raises_clear_error():
    with pytest.raises(FileAccessError) as exc:
        validate_input_file("does_not_exist.pdf")
    assert exc.value.hint


def test_unsupported_extension_rejected(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    with pytest.raises(UnsupportedFileTypeError):
        validate_input_file(str(path))


def test_empty_file_rejected(tmp_path):
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    with pytest.raises(CorruptFileError):
        validate_input_file(str(path))


def test_corrupt_pdf_rejected(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"this is not a pdf at all" * 10)
    with pytest.raises(CorruptFileError):
        extract_deck(str(path))


def test_legacy_ppt_gives_conversion_instruction(tmp_path):
    from extractors import pptx_extractor
    from utils.validation import ExtractionError

    path = tmp_path / "old.ppt"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    with pytest.raises(ExtractionError) as exc:
        pptx_extractor.extract(path, "ppt")
    assert ".pptx" in exc.value.hint


def test_very_short_deck_rejected():
    deck = ExtractedDeck("x.pdf", "pdf", slides=[Slide(1, "T", "a b c")])
    with pytest.raises(EmptyDeckError):
        review_deck(deck, provider=HeuristicProvider())


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def test_parser_extracts_company_and_deal_terms(pptx_deck):
    profile = parse_deck(pptx_deck)
    assert profile.company_name == "Nimbus Health"
    assert profile.fundraising.raise_amount == "$6M"
    assert profile.market.tam == "$12B"
    assert profile.financials.gross_margin == "78%"
    assert profile.slide_count == 14


def test_parser_flags_top_down_market_sizing(pptx_deck):
    profile = parse_deck(pptx_deck)
    assert profile.market.methodology == "top-down"
    assert profile.market.sam is None


def test_go_to_market_is_not_tagged_as_market(pptx_deck):
    """The longest keyword match wins, so 'Go-To-Market' is a GTM slide."""
    profile = parse_deck(pptx_deck)
    assert 9 in profile.detected_sections["gtm"]
    assert 9 not in profile.detected_sections.get("market", [])


def test_parser_detects_no_competitor_claim(pptx_deck):
    profile = parse_deck(pptx_deck)
    assert profile.design_signals["no_competitor_claim_slides"] == [10]


def test_parser_invents_nothing_on_an_empty_deck():
    deck = ExtractedDeck("blank.pdf", "pdf", slides=[
        Slide(i, "", "lorem ipsum dolor sit amet " * 4) for i in range(1, 6)
    ])
    profile = parse_deck(deck)
    assert profile.market.tam is None
    assert profile.fundraising.raise_amount is None
    assert profile.traction == []
    assert "Traction" in profile.missing_sections


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

def test_weights_sum_to_100():
    assert sum(w for _, w in WEIGHTS.values()) == 100


def test_score_is_within_range_for_both_extremes(pptx_deck):
    rich = total_score(heuristic_category_scores(parse_deck(pptx_deck)))
    empty_deck = ExtractedDeck("blank.pdf", "pdf", slides=[
        Slide(i, "", "word " * 30) for i in range(1, 8)
    ])
    poor = total_score(heuristic_category_scores(parse_deck(empty_deck)))
    assert 0 <= poor < rich <= 100


def test_reconcile_accepts_the_object_form(pptx_deck):
    profile = parse_deck(pptx_deck)
    supplied = {key: 20.0 for key in WEIGHTS}
    scores = reconcile(supplied, profile)
    assert all(c.score == 20.0 for c in scores)
    assert total_score(scores) == 20


def test_reconcile_accepts_the_list_form(pptx_deck):
    profile = parse_deck(pptx_deck)
    supplied = [{"category": label, "score": 40, "note": "x"}
                for label, _ in WEIGHTS.values()]
    assert all(c.score == 40.0 for c in reconcile(supplied, profile))


def test_reconcile_falls_back_for_junk_values(pptx_deck):
    profile = parse_deck(pptx_deck)
    baseline = {c.category: c.score for c in heuristic_category_scores(profile)}
    scores = reconcile({"traction": "not a number", "team": 10}, profile)
    by_label = {c.category: c.score for c in scores}
    assert by_label["Team"] == 10.0
    assert by_label["Traction"] == baseline["Traction"]


def test_out_of_range_scores_are_clamped(pptx_deck):
    profile = parse_deck(pptx_deck)
    scores = reconcile({key: 500 for key in WEIGHTS}, profile)
    assert total_score(scores) == 100


# --------------------------------------------------------------------------- #
# Review assembly, validation and evidence control
# --------------------------------------------------------------------------- #

def test_heuristic_review_is_complete(pptx_deck):
    result = review_deck(pptx_deck, provider=HeuristicProvider())
    assert result.company == "Nimbus Health"
    assert 0 <= result.presentation_score <= 100
    assert result.overall_assessment in {"Strong", "Moderate", "Needs Work"}
    assert result.weaknesses and result.priority_improvements
    assert result.bottom_line


def test_heuristic_never_claims_full_confidence(pptx_deck):
    """A rule-based pass cannot judge argument quality, so it is capped."""
    result = review_deck(pptx_deck, provider=HeuristicProvider())
    content = [c for c in result.category_scores
               if c.category != "Presentation / Visual Quality"]
    assert all(c.score <= 78.0 for c in content)


def test_assessment_band_always_matches_the_score():
    for score, expected in ((90, "Strong"), (75, "Strong"), (60, "Moderate"),
                            (55, "Moderate"), (54, "Needs Work"), (0, "Needs Work")):
        result = ReviewResult(company="X", presentation_score=score,
                              overall_assessment="Strong")
        validated, _ = validate_review(result)
        assert validated.overall_assessment == expected


def test_invalid_score_is_clamped_and_flagged():
    result = ReviewResult(company="X", presentation_score=173)
    validated, issues = validate_review(result)
    assert validated.presentation_score == 100
    assert any("clamped" in i for i in issues)


def test_generic_strengths_are_removed():
    result = ReviewResult(
        company="X",
        strengths=[Finding("Great deck overall, very nice"), Finding("Good job")],
    )
    validated, issues = validate_review(result)
    assert validated.strengths == []
    assert any("generic" in i for i in issues)


def test_duplicate_findings_are_deduplicated():
    result = ReviewResult(
        company="X",
        weaknesses=[Finding("The market slide has no bottom-up build."),
                    Finding("The market slide has no bottom-up build!")],
    )
    validated, _ = validate_review(result)
    assert len(validated.weaknesses) == 1


def test_recommendations_are_ordered_high_first():
    result = ReviewResult(company="X", priority_improvements=[
        Recommendation("LOW", "Polish the fonts"),
        Recommendation("HIGH", "State the raise amount"),
        Recommendation("MEDIUM", "Add a GTM slide"),
    ])
    validated, _ = validate_review(result)
    assert [r.priority for r in validated.priority_improvements] == \
        ["HIGH", "MEDIUM", "LOW"]


class _StubProvider(LLMProvider):
    """Returns a fixed payload so assembly logic can be tested in isolation."""

    name = "stub"
    requires_credentials = False

    def __init__(self, payload):
        self.payload = payload

    def analyze_deck(self, deck, profile):
        return self.payload


def _payload(**overrides):
    base = {
        "company": "Nimbus Health",
        "sector": "Healthcare",
        "stage": "Seed",
        "investment_thesis": "A care coordination platform for community hospitals.",
        "category_scores": {key: 50 for key in WEIGHTS},
        "strengths": [],
        "weaknesses": [{"text": "No risk slide is present in the deck.",
                        "evidence": "NOT FOUND", "slides": [],
                        "why_investors_care": "Diligence starts with risk."}],
        "priority_improvements": [{"priority": "HIGH", "text": "Add a risk slide.",
                                   "rationale": "Investors expect it."}],
        "presentation_analysis": "Clean and readable.",
        "flow_verdict": "Flow is fine.",
        "recommended_slide_flow": [],
        "not_found": [],
        "bottom_line": "Worth a first meeting.",
    }
    base.update(overrides)
    return base


def test_fabricated_figures_are_downgraded_to_inferred(pptx_deck):
    """A figure absent from the deck cannot stay labelled FOUND IN DECK."""
    payload = _payload(strengths=[{
        "text": "The company reports $99.9M in annual recurring revenue.",
        "evidence": "FOUND IN DECK", "slides": [7], "why_investors_care": "Scale.",
    }])
    result = review_deck(pptx_deck, provider=_StubProvider(payload))
    assert result.strengths[0].evidence == "INFERRED"
    assert any("Evidence check" in w for w in result.warnings)


def test_real_figures_keep_their_found_label(pptx_deck):
    payload = _payload(strengths=[{
        "text": "ARR grew to $1.4M with 128% net revenue retention.",
        "evidence": "FOUND IN DECK", "slides": [7], "why_investors_care": "Scale.",
    }])
    result = review_deck(pptx_deck, provider=_StubProvider(payload))
    assert result.strengths[0].evidence == "FOUND IN DECK"


def test_slide_references_outside_the_deck_are_dropped(pptx_deck):
    payload = _payload(weaknesses=[{
        "text": "Something is missing here entirely.",
        "evidence": "NOT FOUND", "slides": [3, 99, -1], "why_investors_care": "x",
    }])
    result = review_deck(pptx_deck, provider=_StubProvider(payload))
    assert result.weaknesses[0].slides == [3]


def test_degenerate_output_is_detected():
    assert is_degenerate(None)
    assert is_degenerate({"strengths": [], "weaknesses": [], "priority_improvements": []})
    repeated = {"strengths": [{"text": "same"} for _ in range(6)],
                "weaknesses": [], "priority_improvements": []}
    assert is_degenerate(repeated)
    assert not is_degenerate(_payload())


# --------------------------------------------------------------------------- #
# Report generation
# --------------------------------------------------------------------------- #

def _page_count(path: Path) -> int:
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf  # type: ignore
    doc = pymupdf.open(str(path))
    try:
        return doc.page_count
    finally:
        doc.close()


def test_report_is_exactly_one_page(tmp_path, pptx_deck):
    result = review_deck(pptx_deck, provider=HeuristicProvider())
    out = tmp_path / "review.pdf"
    generate_pdf(result, out)
    assert _page_count(out) == 1


def test_report_fits_one_page_even_when_massively_overfull(tmp_path):
    """Content far beyond a page must be compressed, not spilled onto page 2."""
    filler = ("This is a deliberately long finding that runs well past any sensible "
              "length so the fitting logic has to do real work. ") * 4
    result = ReviewResult(
        company="Overflow Industries International Holdings",
        sector="Enterprise software and platform infrastructure services",
        stage="Series A extension bridge round with participating preferred",
        slide_count=42,
        presentation_score=61,
        overall_assessment="Moderate",
        investment_thesis=filler * 3,
        presentation_analysis=filler * 3,
        bottom_line=filler * 3,
        flow_verdict=filler,
        recommended_slide_flow=["Slide {}".format(i) for i in range(1, 17)],
        strengths=[Finding(filler + str(i), slides=[i], why_investors_care=filler)
                   for i in range(1, 9)],
        weaknesses=[Finding(filler + str(i), slides=[i], why_investors_care=filler)
                    for i in range(1, 9)],
        priority_improvements=(
            [Recommendation("HIGH", filler + str(i), filler) for i in range(1, 6)]
            + [Recommendation("MEDIUM", filler + str(i), filler) for i in range(1, 6)]
            + [Recommendation("LOW", filler + str(i), filler) for i in range(1, 6)]
        ),
        category_scores=[CategoryScore(label, 55, weight)
                         for label, weight in WEIGHTS.values()],
    )
    out = tmp_path / "overflow.pdf"
    generate_pdf(result, out)
    assert _page_count(out) == 1


def test_report_survives_a_nearly_empty_review(tmp_path):
    result = ReviewResult(company="Sparse Co", presentation_score=12,
                          overall_assessment="Needs Work")
    out = tmp_path / "sparse.pdf"
    generate_pdf(result, out)
    assert _page_count(out) == 1


def test_report_handles_markup_characters_without_breaking(tmp_path):
    """Angle brackets and ampersands must not corrupt ReportLab's mini-HTML."""
    result = ReviewResult(
        company="A & B <Holdings>",
        presentation_score=50,
        overall_assessment="Needs Work",
        investment_thesis="Margins are <50% & falling; see R&D spend.",
        strengths=[Finding("Gross margin > 70% & rising in FY24 results.")],
        bottom_line="Revenue < plan & burn > forecast.",
    )
    out = tmp_path / "markup.pdf"
    generate_pdf(result, out)
    assert _page_count(out) == 1

    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf  # type: ignore
    doc = pymupdf.open(str(out))
    try:
        text = doc[0].get_text()
    finally:
        doc.close()
    assert "A & B <HOLDINGS>" in text.upper()
    assert "&amp;" not in text


def test_shorten_never_cuts_at_a_comma():
    text = "The market slide needs a bottom-up build, a SAM, and a named segment list."
    assert not _shorten(text, 40).rstrip("…").endswith(",")


def test_shorten_leaves_short_text_untouched():
    assert _shorten("Short enough.", 200) == "Short enough."


# --------------------------------------------------------------------------- #
# Document structure template
# --------------------------------------------------------------------------- #

def test_template_covers_every_rendered_field():
    from reports.template_document import build_template_result

    result = build_template_result()
    assert result.investment_thesis and result.presentation_analysis
    assert result.bottom_line and result.flow_verdict
    assert len(result.strengths) >= 3
    assert len(result.weaknesses) >= 3
    assert {r.priority for r in result.priority_improvements} == {"HIGH", "MEDIUM", "LOW"}
    assert [c.category for c in result.category_scores] ==         [label for label, _ in WEIGHTS.values()]
    assert result.not_found


def test_template_demonstrates_every_evidence_label():
    from reports.template_document import build_template_result

    labels = {f.evidence for f in build_template_result().weaknesses}
    assert "NOT FOUND" in labels
    assert "FOUND IN DECK" in labels


def test_template_json_matches_the_report_json_keys(tmp_path, pptx_deck):
    """The blank template and a real review must expose identical JSON keys."""
    from reports.template_document import template_json

    real = review_deck(pptx_deck, provider=HeuristicProvider()).to_dict()
    assert set(template_json()) == set(real)


def test_template_renders_to_exactly_one_page(tmp_path):
    from reports.template_document import write_template

    created = write_template(tmp_path)
    assert _page_count(created["pdf"]) == 1
    assert created["json"].exists()


def test_template_contains_no_company_specific_data(tmp_path):
    """The template must be reusable for any company, so it names none."""
    from reports.template_document import write_template

    created = write_template(tmp_path)
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf  # type: ignore
    doc = pymupdf.open(str(created["pdf"]))
    try:
        text = doc[0].get_text()
    finally:
        doc.close()

    for token in ("Nimbus", "Health", "Epic", "Cerner", "Ascension", "Baylor",
                  "$1.4M", "$6M", "$12B", "128%"):
        assert token not in text, "template leaked deck content: {}".format(token)

    assert "[COMPANY NAME]" in text
    assert "INVESTMENT THESIS" in text
    assert "KEY STRENGTHS" in text
    assert "KEY INVESTOR CONCERNS" in text
    assert "PRIORITY IMPROVEMENTS" in text
    assert "BOTTOM LINE" in text


def test_template_shows_blank_score_fields(tmp_path):
    from reports.template_document import write_template

    created = write_template(tmp_path)
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf  # type: ignore
    doc = pymupdf.open(str(created["pdf"]))
    try:
        text = doc[0].get_text()
    finally:
        doc.close()

    assert "/ 100" in text
    assert "0-100" in text            # scorecard cells show the range, not a value
    assert "[N] slides" in text
    assert "STRONG / MODERATE / NEEDS WORK" in text


def test_template_cli_needs_no_deck(tmp_path):
    import app

    assert app.main(["--template", "--output", str(tmp_path), "--quiet"]) == 0
    assert (tmp_path / "investor_presentation_review_TEMPLATE.pdf").exists()
    assert (tmp_path / "investor_presentation_review_TEMPLATE.json").exists()


def test_missing_deck_argument_is_reported(tmp_path):
    import app

    with pytest.raises(SystemExit):
        app.main(["--output", str(tmp_path)])


# --------------------------------------------------------------------------- #
# Utilities and end-to-end
# --------------------------------------------------------------------------- #

def test_sanitize_text_normalises_ligatures_and_controls():
    assert sanitize_text("eﬃcient\x00 “quoted”") == 'efficient "quoted"'


def test_slugify_produces_safe_filenames():
    assert slugify("Nimbus Health, Inc.") == "nimbus_health_inc"
    assert slugify("") == "company"


def test_provider_autoselect_falls_back_to_heuristic(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert get_provider().name == "heuristic"


def test_cli_end_to_end(tmp_path, sample_pptx, monkeypatch):
    import app

    monkeypatch.chdir(tmp_path)
    code = app.main([str(sample_pptx), "--output", str(tmp_path / "out"),
                     "--provider", "heuristic", "--quiet"])
    assert code == 0

    pdf = tmp_path / "out" / "nimbus_health_investor_presentation_review.pdf"
    payload = tmp_path / "out" / "nimbus_health_investor_presentation_review.json"
    assert pdf.exists() and payload.exists()
    assert _page_count(pdf) == 1

    data = json.loads(payload.read_text(encoding="utf-8"))
    for key in ("company", "presentation_score", "overall_assessment", "strengths",
                "weaknesses", "priority_improvements", "presentation_analysis",
                "recommended_slide_flow", "bottom_line"):
        assert key in data


def test_cli_reports_unsupported_file_cleanly(tmp_path, capsys):
    import app

    bad = tmp_path / "deck.docx"
    bad.write_text("nope")
    assert app.main([str(bad), "--output", str(tmp_path)]) == 2
    assert "Error" in capsys.readouterr().err


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

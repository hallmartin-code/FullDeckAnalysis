# Investor Pitch Deck Review

Reads a startup pitch deck (PDF, PPTX or PPT), reviews it the way a Seed–Series A
investor would before deciding whether to take a first meeting, and produces a
polished **one-page PDF review** plus the underlying structured JSON.

The report answers one question: *how effectively does this presentation communicate
an investable opportunity, and what should the company fix first?*

---

## Live deployment

**https://pitch-deck-review-production.up.railway.app** — upload a deck in the
browser, watch the analysis progress step by step, read the full review on screen, and
download the one-page PDF and the JSON. An access code is required; see
[DEPLOY.md](DEPLOY.md).

The interface uses the TEN Capital Network brand system (dark navy, coral/amber/teal,
Sora + Inter + JetBrains Mono). Scores are colour-coded on a brand traffic light —
teal at 75+, amber at 55+, coral below — matching the report's own Strong / Moderate /
Needs Work bands. The page renders its accepted formats, size cap and email disclosure
from `/api/config`, so what a user is told always matches what the server enforces.

Run it locally:

```bash
python -m uvicorn web.main:app --reload --port 8000
```

---

## Quick start

```bash
pip install -r requirements.txt

cp .env.example .env          # add ANTHROPIC_API_KEY (or OPENAI_API_KEY)

python app.py input/deck.pdf
```

```text
Analyzing pitch deck...

Company: Nimbus Health
Slides analyzed: 14

Presentation Effectiveness Score: 66/100  (Moderate)

Generating investor presentation review...

Created:
  output\nimbus_health_investor_presentation_review.pdf
  output\nimbus_health_investor_presentation_review.json
```

No API key? It still runs:

```bash
python app.py input/deck.pptx --provider heuristic
```

That produces an offline **structural** review — which investor-critical sections
exist, how dense the slides are, whether the narrative is in the standard order. It
never pretends to have judged the quality of the argument, and its scores are capped
accordingly.

---

## CLI

```bash
python app.py DECK [options]
```

| Option | Effect |
| --- | --- |
| `-o, --output DIR` | Where to write the report (default `output`) |
| `--provider {anthropic,openai,heuristic}` | Force an analysis backend |
| `--model ID` | Override the model for the chosen provider |
| `--engine {reportlab,html}` | PDF renderer (default `reportlab`) |
| `--template` | Write the blank structure template and exit (no deck needed) |
| `--json-only` | Write the JSON analysis, skip the PDF |
| `--quiet` / `--verbose` | Suppress progress / print full tracebacks |

Exit codes: `0` success, `2` a reported problem with the deck or configuration,
`1` an unexpected error, `130` interrupted.

---

## Document structure template

```bash
python app.py --template
```

Writes `output/investor_presentation_review_TEMPLATE.pdf` and `.json` — the report
with every field replaced by a placeholder describing what belongs in it, and no
company data of any kind. It is rendered through the *same* generator as a real
review, so the blank form and the finished report can never drift apart.

- **`reports/template_document.py`** — the executable definition: every section,
  field and placeholder.
- **`reports/templates/report_structure.md`** — the written specification: page
  setup, palette, typography, section order, per-field character budgets, evidence
  labels, and the exact order in which content is dropped to hold one page.

Use the template to see what the app will produce before running it, to brief a
reviewer on what each field means, or as the reference when changing the report.

---

## Automated report emails

Every completed review is emailed to **Info@tencapital.group** with the one-page PDF
and the JSON attached. The body carries the whole assessment inline — score, category
scorecard, thesis, strengths, concerns, prioritised improvements and bottom line — so
it is readable without opening an attachment.

Delivery is via [Resend](https://resend.com) from `reports@tencapital.group`, a
verified sending domain.

```bash
python app.py deck.pdf                          # emails on completion
python app.py deck.pdf --no-email               # suppress
python app.py deck.pdf --email-to a@x.com,b@x.com   # override recipients
```

Configure with `RESEND_API_KEY`, `REPORT_EMAIL_TO`, `REPORT_EMAIL_FROM`,
`REPORT_EMAIL_CC`, `REPORT_EMAIL_REPLY_TO`, `REPORT_EMAIL_ATTACH_PDF`,
`REPORT_EMAIL_ATTACH_JSON`. Leaving `RESEND_API_KEY` unset disables emailing; the run
then reports `Email skipped: RESEND_API_KEY is not set` rather than failing.

**Sending is best-effort.** A completed review is the product, so a mail failure never
destroys it — the outcome is reported in the CLI output, in the web UI, and in the
job's `email` field.

---

## What the review covers

**Content.** Investment thesis, problem, solution and product, market opportunity,
traction, business model, go-to-market, competitive positioning and defensibility,
financials, the fundraising ask, and team.

**Presentation.** Narrative sequence against the standard investor flow, slide
density, headline quality, image-only slides, small type, and slides likely to be
skimmed.

**Output.** Evidence-cited strengths, investor concerns with the *why investors care*
consequence, and prioritised HIGH/MEDIUM/LOW recommendations specific enough to act
on — "replace the $12B top-down TAM with hospitals in the 100–400 bed band × $198K
ACV", not "improve the market slide".

### Presentation Effectiveness Score

Nine weighted categories summing to 100:

| Category | Weight |
| --- | ---: |
| Investment Thesis / Narrative | 15 |
| Problem / Solution | 15 |
| Traction | 15 |
| Business Model / GTM | 15 |
| Market Opportunity | 10 |
| Competition / Defensibility | 10 |
| Financials / Raise | 10 |
| Team | 5 |
| Presentation / Visual Quality | 5 |

Bands: **Strong** ≥ 75, **Moderate** ≥ 55, **Needs Work** below. The headline number
is always recomputed from the category scores, so it can never disagree with its own
breakdown. The written evidence matters more than the number.

---

## Evidence control

Fabricated numbers are the failure mode that would make this tool worse than useless,
so several defences stack:

1. **Extraction never invents.** Anything unreadable is recorded in `warnings` and
   passed to the model as "you do not know what is on these slides".
2. **Findings are labelled** `FOUND IN DECK`, `INFERRED` or `NOT FOUND`. Absence is a
   first-class finding, never a licence to assume a value.
3. **Figures are verified.** Every currency or percentage figure in a claim marked
   FOUND IN DECK is checked against the deck text. If it is not there, the claim is
   downgraded to INFERRED and the substitution is reported in `warnings`.
4. **Slide references are bounds-checked** against the real slide count.
5. **Generic praise is stripped** — a "strength" with no supporting detail is removed.
6. **Scores are clamped** to 0–100 and the assessment band is derived from the score.

The PDF footer states this, and `[inferred]` / `[not found]` tags appear inline.

---

## Output files

```text
output/
  <company>_investor_presentation_review.pdf     # exactly one page
  <company>_investor_presentation_review.json    # full structured analysis
```

The JSON contains `company`, `presentation_score`, `overall_assessment`,
`investment_thesis`, `strengths`, `weaknesses`, `priority_improvements`,
`presentation_analysis`, `recommended_slide_flow`, `bottom_line`, `category_scores`,
`not_found`, `analysis_source` and `warnings`.

---

## How the one-page guarantee works

The report is built at a series of **density levels**. Level 0 is the full report;
each subsequent level drops the least valuable content first — LOW-priority
recommendations, then supporting rationale, then the *why investors care* clauses,
then the recommended-flow panel — and only reduces type size at the very end.

After each attempt the document is rendered to memory and its **real page count** is
checked. The first level that produces one page wins. Nothing is shrunk until it
fits, and nothing is clipped: content that will not fit is removed in priority order
rather than spilled onto a second page.

---

## Architecture

```text
app.py                      CLI entry point
extractors/
  __init__.py               format detection and dispatch
  pdf_extractor.py          PyMuPDF primary, pdfplumber tables and fallback
  pptx_extractor.py         python-pptx: shapes, groups, tables, charts, notes
analysis/
  deck_parser.py            raw deck  ->  normalized DeckProfile
  llm_provider.py           LLMProvider ABC + Anthropic / OpenAI / heuristic
  prompts.py                system prompt, user prompt, JSON output schema
  scoring.py                weighted score, heuristic baseline, reconciliation
  investor_review.py        orchestration, assembly and evidence control
reports/
  pdf_generator.py          ReportLab one-page renderer (default)
  html_generator.py         Jinja2 + WeasyPrint renderer (optional)
  template_document.py      blank structure template (--template)
  templates/investor_review.html
  templates/report_structure.md
notifications/
  settings.py               email configuration from the environment
  render.py                 email HTML (table-based) and plain-text bodies
  transport.py              Resend delivery, null transport for tests
web/
  main.py                   FastAPI app: upload, poll, download
  jobs.py                   background worker pool and job registry
  static/index.html         single-page front end
models/schemas.py           ExtractedDeck, DeckProfile, ReviewResult
utils/
  validation.py             errors, sanitisation, review QC
  file_utils.py             magic-byte type detection, output helpers
```

### Swapping the LLM

Subclass `LLMProvider`, implement `analyze_deck(deck, profile) -> dict` matching
`prompts.REVIEW_SCHEMA`, and register it in `PROVIDERS`:

```python
class MyProvider(LLMProvider):
    name = "mine"

    def analyze_deck(self, deck, profile):
        return {...}
```

Credentials come from the environment (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) or a
`.env` file. Keys are never hard-coded and never written to the output.

> **Schema note.** The output schema deliberately uses no `enum` constraints, and
> represents category scores as a fixed object rather than an array. Constrained
> decoding was observed to collapse into a repetition loop on an unbounded array with
> an `enum` on its items. Allowed values are stated in the prompt and enforced in
> Python instead, and `is_degenerate()` catches the failure and retries.

---

## Handled failure modes

| Situation | Behaviour |
| --- | --- |
| Unsupported file type | Named error, lists supported formats |
| Password-protected PDF | Explains how to remove the password |
| Corrupt or renamed file | Detected by magic bytes, not extension |
| Legacy binary `.ppt` | Explains how to convert to `.pptx` |
| Image-only / scanned PDF | pdfplumber fallback, then reports limited analysis |
| Deck under 3 slides | Refuses rather than producing a hollow review |
| Missing company name | Falls back to metadata, repeated brand token, filename |
| No financials or ask | Reported as NOT FOUND, never assumed |
| Model returns junk | Detected, retried once, then a clear error |
| Rate limit / bad key | Distinguished by status code, each with a fix |

---

## Tests

```bash
python -m pytest tests/ -q          # 101 tests, no API key required
python tests/make_sample_deck.py    # regenerate the sample decks
```

The suite covers both extractors, format detection by content, parser accuracy,
scoring bounds and reconciliation, evidence downgrading of fabricated figures, review
validation, the one-page guarantee (including a deliberately overfull report), markup
escaping, the structure template (including that it leaks no deck content), the
report email (rendering, attachments, failure handling), and the CLI and web API end
to end. `tests/conftest.py` strips real credentials from every test, so running the
suite can never spend money or send mail. It uses stub providers, so it is deterministic and
free to run.

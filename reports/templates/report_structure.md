# Investor Presentation Review — Document Structure Template

The canonical structure of the one-page report this application generates. It defines
**what** each section contains and **how** it is formatted, with no company-specific
content.

- Executable form: `reports/template_document.py`
- Rendered blank form: `python app.py --template`
- Machine-readable contract: `analysis.prompts.REVIEW_SCHEMA`

Every analysis provider must fill exactly these fields. Renderers must lay out exactly
these sections in this order.

---

## 1. Page specification

| Property | Value |
| --- | --- |
| Page size | US Letter, portrait (8.5 × 11 in) |
| Margins | 0.5 in all sides; 0.5 in + 16 pt reserved at the foot |
| Length | **Exactly one page**, verified by render-and-count — never estimated |
| Background | White |
| Typeface | Helvetica (Bold for headings and emphasis) |
| Body text | 8.6 pt / 10.6 pt leading, reduced to 7.8 pt only as a last resort |
| Section headings | 8.4 pt Bold, uppercase, accent colour, 0.3 pt letter-spacing |
| Company name | 17 pt Bold, uppercase |
| Score | 21 pt Bold, accent colour, right-aligned |
| Dividers | 0.6 pt hairline between sections; 1.1 pt accent rule under the header |

### Palette

| Role | Hex | Used for |
| --- | --- | --- |
| Ink | `#141821` | Company name, bottom-line text |
| Body | `#2B313D` | Body copy |
| Muted | `#6B7280` | Kicker, tags, rationale, footer |
| Accent | `#1F3A5F` | Section headings, score, rules, mid-range bars |
| High | `#9E2B25` | HIGH priority label and numbering, low scores |
| Medium | `#9A6510` | MEDIUM priority label, mid-low scores |
| Low | `#4B5563` | LOW priority label |
| Strong | `#2F5D3A` | Scorecard bars at 75 and above |
| Panel | `#F4F5F7` | Bottom-line panel background |
| Hairline | `#E6E8EC` | Column dividers, empty scorecard track |

---

## 2. Section order

```text
┌──────────────────────────────────────────────────────────────────────┐
│ [COMPANY NAME]                                        __ / 100       │
│ INVESTOR PRESENTATION REVIEW · [Sector] · [Stage] · [N] slides       │
│                                        STRONG / MODERATE / NEEDS WORK│
├══════════════════════════════════════════════════════════════════════┤
│ CATEGORY SCORECARD    9 mini-bars: label · bar · score (weight)      │
├──────────────────────────────────────────────────────────────────────┤
│ INVESTMENT THESIS                                                    │
├──────────────────────────────────────────────────────────────────────┤
│ KEY STRENGTHS                  │  KEY INVESTOR CONCERNS              │
├──────────────────────────────────────────────────────────────────────┤
│ PRIORITY IMPROVEMENTS          HIGH → MEDIUM → LOW, continuously #'d │
├──────────────────────────────────────────────────────────────────────┤
│ PRESENTATION & STORYTELLING    │  RECOMMENDED FLOW                   │
├──────────────────────────────────────────────────────────────────────┤
│ ▌BOTTOM LINE                                          (shaded panel) │
└──────────────────────────────────────────────────────────────────────┘
  footer: provenance · evidence-control note · Page 1 of 1
```

---

## 3. Field reference

### 3.1 Header

| Field | JSON key | Format | Rule |
| --- | --- | --- | --- |
| Company name | `company` | Uppercase, 17 pt | As the deck presents it; `Unknown Company` if never stated |
| Sector | `sector` | Kicker, ≤ 38 chars | Omitted if not determinable |
| Stage | `stage` | Kicker, ≤ 24 chars | Parenthetical detail is dropped to hold one line |
| Slide count | `slide_count` | `[N] slides` | Actual pages/slides extracted |
| Score | `presentation_score` | `NN / 100` | Integer 0–100, recomputed from category scores |
| Assessment | `overall_assessment` | Uppercase | `Strong` ≥ 75, `Moderate` ≥ 55, else `Needs Work`; always derived from the score |

### 3.2 Category scorecard

Nine categories, fixed order, weights summing to 100. Each cell shows an uppercase
label, a bar filled to the score, and `score (weight pts)`.

| # | Category | JSON key | Weight | Assesses |
| --- | --- | --- | ---: | --- |
| 1 | Investment Thesis / Narrative | `thesis_narrative` | 15 | Clarity of the thesis; logical story progression |
| 2 | Problem / Solution | `problem_solution` | 15 | Severity and evidence of the problem; clarity and proof of the solution |
| 3 | Traction | `traction` | 15 | Revenue, customers, pilots, retention, milestones vs. stated stage |
| 4 | Business Model / GTM | `model_gtm` | 15 | Monetisation, unit economics, ICP, channels, repeatability |
| 5 | Market Opportunity | `market` | 10 | TAM/SAM/SOM, sizing method, timing, segmentation |
| 6 | Competition / Defensibility | `competition` | 10 | Named competitors, differentiation, IP, switching costs, moat |
| 7 | Financials / Raise | `financials_raise` | 10 | Projections, burn, runway, ask, instrument, use of funds |
| 8 | Team | `team` | 5 | Founder–market fit, domain credibility, leadership completeness |
| 9 | Presentation / Visual Quality | `presentation` | 5 | Density, readability, headlines, charts, hierarchy |

Bar colour by score: ≥ 75 strong green · ≥ 50 accent · ≥ 30 amber · below red.

### 3.3 Investment thesis

`investment_thesis` — one justified paragraph, budget **470 characters**.

Two to three sentences written for a partner who has not seen the deck: what the
company does, for which customer, the problem removed, current stage and traction, the
business model, the ask, and why this could be venture scale. If the deck does not
support a thesis, say so rather than constructing one.

### 3.4 Key strengths | Key investor concerns

Two equal columns separated by a hairline. **3–5 items each**, budget **210 characters**
per item.

| Element | JSON key | Rendering |
| --- | --- | --- |
| Finding text | `text` | `–` bullet, hanging indent |
| Slide reference | `slides` | `[s7]` or `[s7,9,11]`, muted, max 3 shown |
| Evidence label | `evidence` | `[inferred]` / `[not found]` shown; `FOUND IN DECK` implicit |
| Investor consequence | `why_investors_care` | Trailing italic, muted, ≤ 110 chars |

Every strength must cite a metric, customer, credential or milestone from the deck.
Generic praise and items under four words are removed by the quality-control pass.

Concerns cover: unsupported market sizing, unclear monetisation, missing traction, weak
differentiation, unproven GTM, team gaps, unfunded milestones, and expected information
the deck never provides.

### 3.5 Priority improvements

`priority_improvements` — grouped `HIGH` → `MEDIUM` → `LOW`, numbered continuously
across groups. Budget **210 characters** per item, plus an optional italic rationale of
≤ 100 characters.

| Priority | Meaning | Typical count |
| --- | --- | --- |
| HIGH | Would materially reduce investor interest | 3–5 |
| MEDIUM | Weakens persuasion or credibility | 2–3 |
| LOW | Formatting, density or polish | 0–2 |

Each item names the slide, the replacement content, and the calculation or evidence
required. "Replace the $25B top-down TAM with target hospitals × annual contract
value" — not "improve the market slide".

### 3.6 Presentation & storytelling | Recommended flow

Two equal columns.

- `presentation_analysis` — budget **360 characters**. Narrative progression, jumps,
  repetition, mis-ordered slides; then slide count, average words per slide, dense
  slides, image-only slides, small type and missing headlines.
- `flow_verdict` + `recommended_slide_flow` — one sentence confirming the current flow
  works, or naming the ordering problem. `recommended_slide_flow` stays **empty when
  the current flow is effective**; otherwise 8–12 slide names rendered `A → B → C`.

Default reference sequence: Investment thesis → Problem → Solution → Product → Why now
→ Market → Traction → Business model → Go-to-market → Competition → Defensibility →
Financials → Team → The ask → Use of funds and milestones → Closing thesis.

### 3.7 Bottom line

`bottom_line` — shaded panel with a 2.2 pt accent left border, budget **430 characters**.

Two to three sentences in the investor's voice: would you take the meeting, on what
evidence, and what single change would move the answer.

### 3.8 Footer

Provenance (date and analysis source), the evidence-control note, and `Page 1 of 1`.

---

## 4. Fields carried in JSON only

Present in the `.json` artifact, not rendered on the page.

| Key | Contents |
| --- | --- |
| `not_found` | Information an investor expects that the deck never provides |
| `category_scores[].note` | One-line justification per category |
| `analysis_source` | Provider and model that produced the review |
| `warnings` | Extraction problems and quality-control actions taken |

---

## 5. Evidence labels

| Label | Meaning | Rendering |
| --- | --- | --- |
| `FOUND IN DECK` | Explicitly stated in the deck | No tag (the default) |
| `INFERRED` | A reasonable reading, not stated outright | `[inferred]` |
| `NOT FOUND` | Expected by investors, absent from the deck | `[not found]`, no slide reference |

Never fabricated: revenue, customers, market size, valuation, funding, partnerships,
regulatory progress, IP, financial metrics, team experience. Any currency or percentage
figure in a `FOUND IN DECK` claim is checked against the deck text and downgraded to
`INFERRED` if it is not there.

---

## 6. Compression order

When content exceeds one page it is **removed in priority order**, never shrunk to fit
or clipped. Each step is applied in turn until the page count is one:

1. LOW recommendations, 2 → 1 → 0
2. Recommendation rationale text
3. Concerns 5 → 4, then strengths 5 → 4
4. Finding and recommendation budgets 210 → 175 characters
5. `why_investors_care` clauses
6. Thesis, analysis and bottom-line budgets reduced
7. MEDIUM recommendations 3 → 2
8. Recommended-flow panel
9. Body text 8.6 pt → 8.2 pt
10. Strengths and concerns 4 → 3; HIGH 5 → 4
11. Category scorecard
12. Body text 8.2 pt → 7.8 pt; HIGH → 3, MEDIUM → 2

The header, investment thesis, at least three strengths and concerns, the HIGH
recommendations and the bottom line are never dropped.

"""Builds the report email body.

Email HTML is not web HTML. Outlook renders through Word, Gmail strips <style>
blocks, and flexbox/grid are unusable. So everything here is nested tables with
inline styles, a single 640px column, and no external assets -- the same visual
language as the PDF, expressed in the subset of HTML that email clients honour.

A plain-text alternative is generated alongside, which materially helps deliverability
and is what watch/terminal clients show.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from models.schemas import Priority, ReviewResult

MAX_WIDTH = 640

INK = "#141821"
BODY = "#2B313D"
MUTED = "#6B7280"
HAIRLINE = "#E6E8EC"
ACCENT = "#1F3A5F"
HIGH = "#9E2B25"
MEDIUM = "#9A6510"
LOW = "#4B5563"
STRONG = "#2F5D3A"
PANEL = "#F4F5F7"
PAGE_BG = "#EEF0F3"

PRIORITY_COLOR = {
    Priority.HIGH.value: HIGH,
    Priority.MEDIUM.value: MEDIUM,
    Priority.LOW.value: LOW,
}

FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif")


def escape(text: Any) -> str:
    return (
        str("" if text is None else text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def bar_color(score: float) -> str:
    if score >= 75:
        return STRONG
    if score >= 50:
        return ACCENT
    if score >= 30:
        return MEDIUM
    return HIGH


# --------------------------------------------------------------------------- #
# Subject
# --------------------------------------------------------------------------- #

def build_subject(result: ReviewResult) -> str:
    return "[Deck Review] {} - {}/100 {}".format(
        result.company, result.presentation_score, result.overall_assessment
    )


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #

def build_html(result: ReviewResult, extra: Optional[Dict[str, Any]] = None) -> str:
    extra = extra or {}
    meta = " &middot; ".join(
        escape(b) for b in (result.sector, result.stage,
                            "{} slides".format(result.slide_count)) if b
    )

    sections: List[str] = [
        _header(result, meta),
        _scorecard(result),
        _paragraph_section("Investment thesis", result.investment_thesis),
        _findings_section("Key strengths", result.strengths),
        _findings_section("Key investor concerns", result.weaknesses),
        _recommendations(result),
        _paragraph_section("Presentation &amp; storytelling", result.presentation_analysis),
        _bottom_line(result),
        _attachments_note(extra),
        _footer(result, extra),
    ]

    return """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{subject}</title></head>
<body style="margin:0;padding:0;background:{bg};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{bg};padding:24px 12px;">
<tr><td align="center">
<table role="presentation" width="{w}" cellpadding="0" cellspacing="0" border="0"
       style="width:{w}px;max-width:100%;background:#ffffff;border:1px solid {hair};">
{body}
</table>
</td></tr></table>
</body></html>""".format(
        subject=escape(build_subject(result)),
        bg=PAGE_BG,
        hair=HAIRLINE,
        w=MAX_WIDTH,
        body="".join(s for s in sections if s),
    )


def _header(result: ReviewResult, meta: str) -> str:
    return """<tr><td style="background:{accent};padding:20px 24px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td style="font:700 20px/24px {font};color:#ffffff;text-transform:uppercase;">{company}</td>
<td align="right" style="font:700 26px/26px {font};color:#ffffff;white-space:nowrap;">
{score}<span style="font-size:13px;font-weight:400;"> / 100</span></td>
</tr><tr>
<td style="padding-top:5px;font:400 11px/15px {font};color:#C3CEDC;
letter-spacing:.5px;text-transform:uppercase;">Investor Presentation Review</td>
<td align="right" style="padding-top:5px;font:700 11px/15px {font};color:#C3CEDC;
letter-spacing:.7px;text-transform:uppercase;">{band}</td>
</tr><tr>
<td colspan="2" style="padding-top:3px;font:400 12px/16px {font};color:#9FB0C4;">{meta}</td>
</tr></table></td></tr>""".format(
        accent=ACCENT, font=FONT,
        company=escape(result.company),
        score=result.presentation_score,
        band=escape(result.overall_assessment),
        meta=meta,
    )


def _scorecard(result: ReviewResult) -> str:
    if not result.category_scores:
        return ""
    rows = []
    for category in result.category_scores:
        score = max(0.0, min(100.0, float(category.score)))
        rows.append(
            """<tr>
<td style="padding:3px 0;font:400 12px/16px {font};color:{body};width:46%;">{label}</td>
<td style="padding:3px 8px;width:38%;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:{hair};"><tr>
    <td style="font-size:0;line-height:6px;height:6px;">
      <table role="presentation" width="{pct}%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="background:{color};font-size:0;line-height:6px;height:6px;">&nbsp;</td>
      </tr></table>
    </td>
  </tr></table>
</td>
<td align="right" style="padding:3px 0;font:600 12px/16px {font};color:{ink};
white-space:nowrap;">{value} <span style="font-weight:400;color:{muted};">({weight} pts)</span></td>
</tr>""".format(
                font=FONT, body=BODY, hair=HAIRLINE, ink=INK, muted=MUTED,
                label=escape(category.category),
                pct=round(score),
                color=bar_color(score),
                value=round(score),
                weight=category.weight,
            )
        )
    return _section("Category scorecard",
                    '<table role="presentation" width="100%" cellpadding="0" '
                    'cellspacing="0" border="0">{}</table>'.format("".join(rows)))


def _paragraph_section(title: str, text: str) -> str:
    if not text:
        return ""
    return _section(title, '<p style="margin:0;font:400 14px/20px {font};color:{body};">'
                           "{text}</p>".format(font=FONT, body=BODY, text=escape(text)))


def _findings_section(title: str, findings) -> str:
    if not findings:
        return ""
    items = []
    for finding in findings:
        tags = ""
        if finding.slides:
            tags += ' <span style="color:#8A9099;font-size:11px;">[s{}]</span>'.format(
                ",".join(str(s) for s in finding.slides[:3]))
        if finding.evidence and finding.evidence != "FOUND IN DECK":
            tags += ' <span style="color:#8A9099;font-size:11px;">[{}]</span>'.format(
                escape(finding.evidence.lower()))
        why = ""
        if finding.why_investors_care:
            why = ' <span style="color:{muted};font-style:italic;">{t}</span>'.format(
                muted=MUTED, t=escape(finding.why_investors_care))
        items.append(
            """<tr>
<td valign="top" style="padding:0 6px 7px 0;font:400 14px/20px {font};color:{muted};">&ndash;</td>
<td valign="top" style="padding:0 0 7px 0;font:400 14px/20px {font};color:{body};">{text}{tags}{why}</td>
</tr>""".format(font=FONT, muted=MUTED, body=BODY,
                text=escape(finding.text), tags=tags, why=why)
        )
    return _section(title,
                    '<table role="presentation" width="100%" cellpadding="0" '
                    'cellspacing="0" border="0">{}</table>'.format("".join(items)))


def _recommendations(result: ReviewResult) -> str:
    if not result.priority_improvements:
        return ""
    blocks, number = [], 1
    for priority in (Priority.HIGH.value, Priority.MEDIUM.value, Priority.LOW.value):
        items = result.by_priority(priority)
        if not items:
            continue
        colour = PRIORITY_COLOR[priority]
        blocks.append(
            '<tr><td colspan="2" style="padding:8px 0 4px;font:700 11px/14px {font};'
            'color:{colour};letter-spacing:.5px;">{priority}</td></tr>'.format(
                font=FONT, colour=colour, priority=priority)
        )
        for item in items:
            rationale = ""
            if item.rationale:
                rationale = ' <span style="color:{muted};font-style:italic;">{t}</span>'.format(
                    muted=MUTED, t=escape(item.rationale))
            blocks.append(
                """<tr>
<td valign="top" style="padding:0 7px 7px 0;font:700 14px/20px {font};color:{colour};
white-space:nowrap;">{n}.</td>
<td valign="top" style="padding:0 0 7px 0;font:400 14px/20px {font};color:{body};">{text}{rationale}</td>
</tr>""".format(font=FONT, colour=colour, body=BODY, n=number,
                text=escape(item.text), rationale=rationale)
            )
            number += 1
    return _section("Priority improvements",
                    '<table role="presentation" width="100%" cellpadding="0" '
                    'cellspacing="0" border="0">{}</table>'.format("".join(blocks)))


def _bottom_line(result: ReviewResult) -> str:
    if not result.bottom_line:
        return ""
    return """<tr><td style="padding:4px 24px 20px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{panel};border-left:3px solid {accent};">
<tr><td style="padding:12px 14px;font:400 14px/20px {font};color:{ink};">
<strong style="letter-spacing:.5px;">BOTTOM LINE</strong>&nbsp;&nbsp;{text}
</td></tr></table></td></tr>""".format(
        panel=PANEL, accent=ACCENT, font=FONT, ink=INK,
        text=escape(result.bottom_line),
    )


def _attachments_note(extra: Dict[str, Any]) -> str:
    names = extra.get("attachments") or []
    if not names:
        return ""
    return """<tr><td style="padding:0 24px 18px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="border:1px dashed {hair};"><tr>
<td style="padding:10px 14px;font:400 12px/17px {font};color:{muted};">
<strong style="color:{body};">Attached</strong>&nbsp; {files}
</td></tr></table></td></tr>""".format(
        hair="#C9CDD4", font=FONT, muted=MUTED, body=BODY,
        files=escape(" &middot; ".join(names)).replace("&amp;middot;", "&middot;"),
    )


def _footer(result: ReviewResult, extra: Dict[str, Any]) -> str:
    bits = ["Generated by the Investor Presentation Review app"]
    if result.analysis_source:
        bits.append("via {}".format(result.analysis_source))
    if extra.get("source_filename"):
        bits.append("from {}".format(extra["source_filename"]))
    if extra.get("origin"):
        bits.append("({})".format(extra["origin"]))

    warnings = ""
    notes = (result.warnings or [])[:4]
    if notes:
        warnings = (
            '<div style="margin-top:8px;color:{muted};"><strong>Notes</strong><br>{n}</div>'
        ).format(muted=MUTED, n="<br>".join("&ndash; " + escape(w) for w in notes))

    return """<tr><td style="padding:14px 24px 20px;border-top:1px solid {hair};
font:400 11px/16px {font};color:{muted};">
{bits}.<br>
Evidence-controlled review: claims marked [inferred] or [not found] are not stated in
the deck.{warnings}
</td></tr>""".format(
        hair=HAIRLINE, font=FONT, muted=MUTED,
        bits=escape(" ".join(bits)), warnings=warnings,
    )


def _section(title: str, inner_html: str) -> str:
    return """<tr><td style="padding:18px 24px 0;">
<div style="font:700 11px/14px {font};color:{accent};letter-spacing:.7px;
text-transform:uppercase;padding-bottom:8px;border-bottom:1px solid {hair};
margin-bottom:10px;">{title}</div>
{inner}
</td></tr>""".format(font=FONT, accent=ACCENT, hair=HAIRLINE,
                     title=title, inner=inner_html)


# --------------------------------------------------------------------------- #
# Plain text alternative
# --------------------------------------------------------------------------- #

def build_text(result: ReviewResult, extra: Optional[Dict[str, Any]] = None) -> str:
    extra = extra or {}
    lines: List[str] = []
    add = lines.append

    meta = " | ".join(b for b in (result.sector, result.stage,
                                  "{} slides".format(result.slide_count)) if b)
    add(result.company.upper())
    add("INVESTOR PRESENTATION REVIEW")
    if meta:
        add(meta)
    add("")
    add("SCORE: {}/100 ({})".format(result.presentation_score, result.overall_assessment))
    add("")

    if result.category_scores:
        add("CATEGORY SCORECARD")
        for category in result.category_scores:
            add("  {:<32} {:>3.0f}/100  ({} pts)".format(
                category.category[:32], category.score, category.weight))
        add("")

    def block(title: str, body: str) -> None:
        if body:
            add(title)
            add(body)
            add("")

    block("INVESTMENT THESIS", result.investment_thesis)

    for title, findings in (("KEY STRENGTHS", result.strengths),
                            ("KEY INVESTOR CONCERNS", result.weaknesses)):
        if not findings:
            continue
        add(title)
        for finding in findings:
            suffix = ""
            if finding.slides:
                suffix += " [s{}]".format(",".join(str(s) for s in finding.slides[:3]))
            if finding.evidence and finding.evidence != "FOUND IN DECK":
                suffix += " [{}]".format(finding.evidence.lower())
            add("  - {}{}".format(finding.text, suffix))
        add("")

    if result.priority_improvements:
        add("PRIORITY IMPROVEMENTS")
        number = 1
        for priority in (Priority.HIGH.value, Priority.MEDIUM.value, Priority.LOW.value):
            items = result.by_priority(priority)
            if not items:
                continue
            add("  {}".format(priority))
            for item in items:
                add("  {}. {}".format(number, item.text))
                number += 1
        add("")

    block("PRESENTATION & STORYTELLING", result.presentation_analysis)
    block("BOTTOM LINE", result.bottom_line)

    names = extra.get("attachments") or []
    if names:
        add("ATTACHED: " + ", ".join(names))
        add("")

    add("Generated by the Investor Presentation Review app"
        + (" via {}".format(result.analysis_source) if result.analysis_source else ""))
    add("Claims marked [inferred] or [not found] are not stated in the deck.")
    return "\n".join(lines)

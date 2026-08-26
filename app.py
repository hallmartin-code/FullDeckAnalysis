"""Investor Pitch Deck Review -- command line entry point.

    python app.py deck.pdf
    python app.py deck.pptx --output ./output
    python app.py deck.pdf --provider heuristic     # no API key needed
"""

from __future__ import annotations

import argparse
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

from analysis.investor_review import review_deck
from analysis.llm_provider import PROVIDERS, get_provider
from extractors import extract_deck
from models.schemas import ReviewResult, slugify
from reports.pdf_generator import generate_pdf
from utils.file_utils import ensure_output_dir, write_json
from utils.validation import DeckReviewError

APP_NAME = "Investor Presentation Review"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.py",
        description="Review a startup pitch deck as an investor and produce a "
                    "one-page PDF assessment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python app.py input/deck.pdf\n"
               "  python app.py input/deck.pptx --output ./output\n"
               "  python app.py input/deck.pdf --provider heuristic\n",
    )
    parser.add_argument("deck", nargs="?",
                        help="Path to the pitch deck (.pdf, .pptx or .ppt)")
    parser.add_argument("--template", action="store_true",
                        help="Write the blank report structure template (PDF + JSON) "
                             "and exit. No deck required.")
    parser.add_argument("-o", "--output", default="output",
                        help="Directory for the generated report (default: output)")
    parser.add_argument("--provider", choices=sorted(PROVIDERS),
                        help="LLM backend. Default: anthropic, then openai, then "
                             "heuristic, based on available credentials.")
    parser.add_argument("--model", help="Override the model id for the chosen provider")
    parser.add_argument("--engine", choices=("reportlab", "html"), default="reportlab",
                        help="PDF rendering engine. 'html' uses Jinja2 + WeasyPrint and "
                             "needs system GTK libraries (default: reportlab)")
    parser.add_argument("--json-only", action="store_true",
                        help="Write the JSON analysis but skip the PDF")
    parser.add_argument("--no-email", action="store_true",
                        help="Do not email the result, even if RESEND_API_KEY is set")
    parser.add_argument("--email-to",
                        help="Override the report recipient(s), comma separated")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    parser.add_argument("--verbose", action="store_true",
                        help="Print full tracebacks on failure")
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    say = (lambda *_a, **_k: None) if args.quiet else _say

    try:
        if args.template:
            from reports.template_document import write_template

            say("\nWriting report structure template...\n")
            created = write_template(args.output, engine=args.engine)
            say("Created:")
            for path in (created["pdf"], created["json"]):
                say("  {}".format(_display_path(path)))
            say("")
            return 0

        if not args.deck:
            build_parser().error("a deck path is required (or use --template)")

        say("\nAnalyzing pitch deck...\n")

        deck = extract_deck(args.deck)
        for warning in deck.warnings:
            say("  ! {}".format(warning))

        provider = get_provider(args.provider, args.model)
        if provider.name == "heuristic" and not args.provider:
            say("  ! No API key found (ANTHROPIC_API_KEY / OPENAI_API_KEY).")
            say("    Falling back to the offline structural review.")

        result: ReviewResult = review_deck(deck, provider=provider)

        say("Company: {}".format(result.company))
        say("Slides analyzed: {}".format(result.slide_count))
        say("")
        say("Presentation Effectiveness Score: {}/100  ({})".format(
            result.presentation_score, result.overall_assessment))
        say("")

        out_dir = ensure_output_dir(args.output)
        stem = "{}_investor_presentation_review".format(slugify(result.company))

        json_path = write_json(result.to_dict(), out_dir / (stem + ".json"))

        created = [json_path]
        if not args.json_only:
            say("Generating investor presentation review...")
            render = generate_pdf
            if args.engine == "html":
                from reports import html_generator

                render = html_generator.generate_pdf
            info = render(result, out_dir / (stem + ".pdf"))
            created.insert(0, Path(info["path"]))
            if info["density_level"] not in (0, "fallback"):
                say("  (content compressed to level {} to fit one page)".format(
                    info["density_level"]))

        say("\nCreated:")
        for path in created:
            say("  {}".format(_display_path(path)))

        if not args.no_email:
            say("")
            say(_send_email(result, created, args).summary)

        qc = [w for w in result.warnings if w]
        if qc and not args.quiet:
            say("\nNotes:")
            for note in qc[:6]:
                say("  - {}".format(note))
        say("")
        return 0

    except DeckReviewError as exc:
        _fail(str(exc), getattr(exc, "hint", ""), args.verbose)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - last line of defence for the CLI
        _fail("Unexpected error: {}".format(exc),
              "Re-run with --verbose for the full traceback.", args.verbose)
        return 1


def _send_email(result, created, args):
    """Email the report. Best effort -- the files on disk are the real deliverable."""
    from notifications import EmailSettings, send_review_notification

    settings = EmailSettings.from_env()
    if args.email_to:
        wanted = [a.strip() for a in args.email_to.replace(";", ",").split(",")]
        settings = replace(settings, recipients=[a for a in wanted if a])

    return send_review_notification(
        result,
        pdf_path=next((p for p in created if p.suffix == ".pdf"), None),
        json_path=next((p for p in created if p.suffix == ".json"), None),
        settings=settings,
        source_filename=Path(args.deck).name,
        origin="command line",
    )


def _say(message: str = "") -> None:
    print(message, flush=True)


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _fail(message: str, hint: str, verbose: bool) -> None:
    print("\nError: {}".format(message), file=sys.stderr)
    if hint:
        print("       {}".format(hint), file=sys.stderr)
    if verbose:
        print("", file=sys.stderr)
        traceback.print_exc()


if __name__ == "__main__":
    sys.exit(main())

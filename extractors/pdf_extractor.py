"""PDF pitch-deck extraction.

PyMuPDF is the primary engine (fast, gives font metrics and image counts).
pdfplumber is used as a table extractor and as a text fallback when PyMuPDF
returns nothing. Neither is allowed to fail the whole run on its own -- every
degradation is recorded in ``ExtractedDeck.warnings``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from models.schemas import ExtractedDeck, Slide
from utils.validation import (
    CorruptFileError,
    EncryptedFileError,
    ExtractionError,
    sanitize_text,
)

# Table extraction is the slow part; cap it so huge decks stay responsive.
MAX_TABLE_PAGES = 40
TITLE_MAX_CHARS = 90


def _import_pymupdf():
    try:
        import pymupdf  # type: ignore

        return pymupdf
    except ImportError:
        try:
            import fitz  # type: ignore

            return fitz
        except ImportError:
            return None


def extract(path: Path) -> ExtractedDeck:
    """Extract every page of a PDF into an :class:`ExtractedDeck`."""
    deck = ExtractedDeck(source_path=str(path), file_type="pdf", extractor="pymupdf")

    pymupdf = _import_pymupdf()
    if pymupdf is None:
        deck.extractor = "pdfplumber"
        return _extract_with_pdfplumber(path, deck)

    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:  # noqa: BLE001 - library raises many distinct types
        raise CorruptFileError(
            "Could not open {}: {}".format(path.name, exc),
            "The PDF may be corrupted. Try re-exporting it.",
        ) from exc

    try:
        if doc.needs_pass:
            raise EncryptedFileError(
                "{} is password-protected.".format(path.name),
                "Remove the password (File > Export without encryption) and retry.",
            )

        deck.metadata = _clean_metadata(doc.metadata or {})

        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            text, font_stats = _page_text_and_fonts(page)
            slide = Slide(
                index=page_index + 1,
                title=_guess_title(text),
                text=sanitize_text(text),
                image_count=_count_images(page),
            )
            slide.word_count = len(slide.text.split())
            slide.is_text_sparse = slide.word_count < 12 and slide.image_count > 0
            deck.slides.append(slide)
            deck.metadata.setdefault("font_stats", []).append(font_stats)
    finally:
        doc.close()

    if not deck.slides:
        raise ExtractionError(
            "{} contains no pages.".format(path.name),
            "Confirm the export completed successfully.",
        )

    _attach_tables(path, deck)

    if not deck.has_usable_text:
        deck.warnings.append(
            "Little or no selectable text found ({} words across {} pages) -- the deck "
            "is likely image-only or scanned. Analysis will be limited to structure.".format(
                deck.total_words, deck.slide_count
            )
        )
        _plumber_text_fallback(path, deck)

    return deck


# --------------------------------------------------------------------------- #
# PyMuPDF helpers
# --------------------------------------------------------------------------- #

def _page_text_and_fonts(page) -> Tuple[str, Dict[str, Any]]:
    """Return page text plus font-size statistics used by the design review."""
    stats: Dict[str, Any] = {"sizes": [], "small_spans": 0, "max_size": 0.0}
    lines: List[str] = []

    try:
        data = page.get_text("dict")
    except Exception:  # noqa: BLE001
        return page.get_text("text") or "", stats

    for block in data.get("blocks", []):
        if block.get("type") != 0:  # 0 == text block
            continue
        for line in block.get("lines", []):
            chunks = []
            for span in line.get("spans", []):
                span_text = span.get("text", "")
                if not span_text.strip():
                    continue
                chunks.append(span_text)
                size = round(float(span.get("size", 0)), 1)
                stats["sizes"].append(size)
                if size < 10:
                    stats["small_spans"] += 1
                stats["max_size"] = max(stats["max_size"], size)
            if chunks:
                lines.append("".join(chunks).strip())

    if stats["sizes"]:
        stats["avg_size"] = round(sum(stats["sizes"]) / len(stats["sizes"]), 1)
        stats["min_size"] = min(stats["sizes"])
    stats.pop("sizes", None)  # keep metadata small
    return "\n".join(lines), stats


def _count_images(page) -> int:
    try:
        return len(page.get_images(full=True))
    except Exception:  # noqa: BLE001
        return 0


def _clean_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("title", "author", "subject", "creator", "producer")
    return {k: sanitize_text(str(v)) for k, v in meta.items() if k in keep and v}


# --------------------------------------------------------------------------- #
# pdfplumber helpers
# --------------------------------------------------------------------------- #

def _open_plumber(path: Path):
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return None, "pdfplumber is not installed; tables were not extracted."
    try:
        return pdfplumber.open(str(path)), None
    except Exception as exc:  # noqa: BLE001
        return None, "pdfplumber could not open the file: {}".format(exc)


def _attach_tables(path: Path, deck: ExtractedDeck) -> None:
    pdf, warning = _open_plumber(path)
    if pdf is None:
        if warning:
            deck.warnings.append(warning)
        return
    try:
        for page_index, page in enumerate(pdf.pages[:MAX_TABLE_PAGES]):
            if page_index >= len(deck.slides):
                break
            try:
                tables = page.extract_tables() or []
            except Exception:  # noqa: BLE001
                continue
            cleaned = [_clean_table(t) for t in tables]
            deck.slides[page_index].tables = [t for t in cleaned if t]
        if len(pdf.pages) > MAX_TABLE_PAGES:
            deck.warnings.append(
                "Table extraction limited to the first {} pages.".format(MAX_TABLE_PAGES)
            )
    finally:
        pdf.close()


def _plumber_text_fallback(path: Path, deck: ExtractedDeck) -> None:
    """Second opinion on text when PyMuPDF found almost nothing."""
    pdf, _ = _open_plumber(path)
    if pdf is None:
        return
    recovered = 0
    try:
        for page_index, page in enumerate(pdf.pages):
            if page_index >= len(deck.slides):
                break
            slide = deck.slides[page_index]
            if slide.word_count >= 12:
                continue
            try:
                text = sanitize_text(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                continue
            if len(text.split()) > slide.word_count:
                slide.text = text
                slide.title = _guess_title(text)
                slide.word_count = len(text.split())
                slide.is_text_sparse = slide.word_count < 12 and slide.image_count > 0
                recovered += 1
    finally:
        pdf.close()
    if recovered:
        deck.warnings.append(
            "Recovered text on {} page(s) using the pdfplumber fallback.".format(recovered)
        )
        deck.extractor = "pymupdf+pdfplumber"


def _extract_with_pdfplumber(path: Path, deck: ExtractedDeck) -> ExtractedDeck:
    """Used only when PyMuPDF is unavailable."""
    pdf, warning = _open_plumber(path)
    if pdf is None:
        raise ExtractionError(
            warning or "No PDF extraction backend is available.",
            "Install PyMuPDF: pip install pymupdf",
        )
    deck.warnings.append("PyMuPDF unavailable; used pdfplumber only (no font metrics).")
    try:
        for page_index, page in enumerate(pdf.pages):
            text = sanitize_text(page.extract_text() or "")
            slide = Slide(
                index=page_index + 1,
                title=_guess_title(text),
                text=text,
                image_count=len(getattr(page, "images", []) or []),
            )
            try:
                slide.tables = [t for t in (_clean_table(t) for t in page.extract_tables() or []) if t]
            except Exception:  # noqa: BLE001
                pass
            deck.slides.append(slide)
    finally:
        pdf.close()
    if not deck.slides:
        raise ExtractionError("{} contains no pages.".format(path.name))
    return deck


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _clean_table(table: Optional[List[List[Any]]]) -> List[List[str]]:
    if not table:
        return []
    rows: List[List[str]] = []
    for row in table:
        cells = [sanitize_text(str(c)) if c is not None else "" for c in row]
        if any(cells):
            rows.append(cells)
    return rows if len(rows) > 1 else []


def _guess_title(text: str) -> str:
    """First short, non-numeric line -- a good proxy for the slide headline."""
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or len(line) > TITLE_MAX_CHARS:
            continue
        if line.replace(".", "").isdigit():
            continue
        return sanitize_text(line)
    return ""

"""Format detection and dispatch to the right extractor."""

from __future__ import annotations

from pathlib import Path

from models.schemas import ExtractedDeck
from utils.file_utils import detect_file_type
from utils.validation import UnsupportedFileTypeError, validate_input_file


def extract_deck(path: str) -> ExtractedDeck:
    """Validate the input file, detect its real format and extract its contents."""
    file_path: Path = validate_input_file(path)
    file_type = detect_file_type(file_path)

    if file_type == "pdf":
        from extractors import pdf_extractor

        return pdf_extractor.extract(file_path)

    if file_type in {"pptx", "ppt"}:
        from extractors import pptx_extractor

        return pptx_extractor.extract(file_path, file_type)

    raise UnsupportedFileTypeError(
        "Unsupported file type: {}".format(file_type),
        "Supported formats are PDF, PPTX and PPT.",
    )


__all__ = ["extract_deck"]

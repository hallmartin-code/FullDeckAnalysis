"""File type detection and output helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from utils.validation import CorruptFileError, UnsupportedFileTypeError

# Magic bytes. PPTX/DOCX/XLSX are all ZIP containers; PPT is an OLE2 compound file.
_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def detect_file_type(path: Path) -> str:
    """Return ``pdf``, ``pptx`` or ``ppt``, trusting content over the extension."""
    with open(path, "rb") as handle:
        header = handle.read(8)

    suffix = path.suffix.lower()

    if header.startswith(_PDF_MAGIC):
        return "pdf"
    if any(header.startswith(m) for m in _ZIP_MAGIC):
        return "pptx"
    if header.startswith(_OLE2_MAGIC):
        return "ppt"

    # Header was inconclusive (some PDFs carry leading junk before %PDF-).
    if suffix == ".pdf":
        with open(path, "rb") as handle:
            if _PDF_MAGIC in handle.read(4096):
                return "pdf"
        raise CorruptFileError(
            "{} has a .pdf extension but no PDF header.".format(path.name),
            "The file may be corrupted or renamed. Re-export it.",
        )
    if suffix in {".pptx", ".ppt"}:
        raise CorruptFileError(
            "{} does not look like a valid PowerPoint file.".format(path.name),
            "Open it in PowerPoint and re-save as .pptx.",
        )
    raise UnsupportedFileTypeError(
        "Could not determine the file type of {}.".format(path.name),
        "Supported formats are PDF, PPTX and PPT.",
    )


def ensure_output_dir(path: str) -> Path:
    out = Path(path).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_json(data: Dict[str, Any], destination: Path) -> Path:
    destination.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return destination


def human_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return "{:.0f} {}".format(num_bytes, unit)
        num_bytes /= 1024.0
    return "{} B".format(num_bytes)

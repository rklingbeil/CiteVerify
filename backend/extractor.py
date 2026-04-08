"""Document text extraction — PDF and DOCX."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    text: str
    page_count: int | None = None
    warnings: list[str] = field(default_factory=list)


def extract_pdf(file_path: str) -> ExtractionResult:
    """Extract text from a PDF file using PyMuPDF."""
    import fitz  # PyMuPDF

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    warnings: list[str] = []
    pages: list[str] = []

    with fitz.open(file_path) as doc:
        for i, page in enumerate(doc):
            text = page.get_text("text")
            if text.strip():
                pages.append(text)
            else:
                warnings.append(f"Page {i + 1}: no text extracted (may be scanned/image)")

    full_text = "\n\n".join(pages)
    if not full_text.strip():
        raise ValueError("No text could be extracted from the PDF")

    return ExtractionResult(
        text=full_text,
        page_count=len(pages),
        warnings=warnings,
    )


def extract_docx(file_path: str) -> ExtractionResult:
    """Extract text from a DOCX file."""
    from docx import Document

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    doc = Document(file_path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]

    full_text = "\n\n".join(paragraphs)
    if not full_text.strip():
        raise ValueError("No text could be extracted from the DOCX")

    return ExtractionResult(text=full_text)


def extract_document(file_path: str) -> ExtractionResult:
    """Extract text from a document, dispatching by file extension."""
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return extract_pdf(file_path)
    elif ext in (".docx", ".doc"):
        return extract_docx(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

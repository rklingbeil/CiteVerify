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
        if doc.is_encrypted:
            raise ValueError("PDF is password-protected. Please provide an unencrypted PDF.")
        total_pages = len(doc)
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
        page_count=total_pages,
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

    # Extract footnotes and endnotes (legal citations often appear here)
    footnote_texts = []
    try:
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        # Footnotes
        footnotes_part = doc.part.rels.get(RT.FOOTNOTES)
        if footnotes_part:
            for fn in footnotes_part.target_part.element.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}footnote'):
                fn_text = "".join(t.text or "" for t in fn.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
                if fn_text.strip():
                    footnote_texts.append(fn_text)
        # Endnotes
        endnotes_part = doc.part.rels.get(RT.ENDNOTES)
        if endnotes_part:
            for en in endnotes_part.target_part.element.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}endnote'):
                en_text = "".join(t.text or "" for t in en.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
                if en_text.strip():
                    footnote_texts.append(en_text)
    except Exception:
        logger.warning("Could not extract footnotes/endnotes from DOCX")

    all_text = paragraphs
    if footnote_texts:
        all_text.append("\n\n--- FOOTNOTES/ENDNOTES ---\n")
        all_text.extend(footnote_texts)

    full_text = "\n\n".join(all_text)
    if not full_text.strip():
        raise ValueError("No text could be extracted from the DOCX")

    return ExtractionResult(text=full_text)


def extract_document(file_path: str) -> ExtractionResult:
    """Extract text from a document, dispatching by file extension."""
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return extract_pdf(file_path)
    elif ext == ".docx":
        return extract_docx(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

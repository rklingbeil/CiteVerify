"""Tests for document text extraction."""

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from backend.extractor import ExtractionResult, extract_document, extract_docx, extract_pdf


class TestExtractPDF:
    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            extract_pdf("/nonexistent/file.pdf")

    def test_extracts_text_from_pages(self):
        page1 = MagicMock()
        page1.get_text.return_value = "Page one text."
        page2 = MagicMock()
        page2.get_text.return_value = "Page two text."

        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([page1, page2])
        mock_doc.__enter__ = lambda self: self
        mock_doc.__exit__ = lambda *_: None

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"fake pdf")
            path = f.name

        try:
            with patch.dict(sys.modules, {"fitz": mock_fitz}):
                result = extract_pdf(path)
            assert isinstance(result, ExtractionResult)
            assert "Page one text." in result.text
            assert "Page two text." in result.text
            assert result.page_count == 2
            assert result.warnings == []
        finally:
            os.unlink(path)

    def test_warns_on_empty_page(self):
        page1 = MagicMock()
        page1.get_text.return_value = "Some text."
        page2 = MagicMock()
        page2.get_text.return_value = "   "

        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([page1, page2])
        mock_doc.__enter__ = lambda self: self
        mock_doc.__exit__ = lambda *_: None

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"fake pdf")
            path = f.name

        try:
            with patch.dict(sys.modules, {"fitz": mock_fitz}):
                result = extract_pdf(path)
            assert len(result.warnings) == 1
            assert "Page 2" in result.warnings[0]
        finally:
            os.unlink(path)

    def test_raises_on_no_text(self):
        page = MagicMock()
        page.get_text.return_value = "  "

        mock_doc = MagicMock()
        mock_doc.__iter__ = lambda self: iter([page])
        mock_doc.__enter__ = lambda self: self
        mock_doc.__exit__ = lambda *_: None

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"fake pdf")
            path = f.name

        try:
            with patch.dict(sys.modules, {"fitz": mock_fitz}):
                with pytest.raises(ValueError, match="No text could be extracted"):
                    extract_pdf(path)
        finally:
            os.unlink(path)


class TestExtractDocx:
    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            extract_docx("/nonexistent/file.docx")


class TestExtractDocument:
    def test_unsupported_extension(self):
        with pytest.raises(ValueError, match="Unsupported file type"):
            extract_document("/some/file.txt")

    @patch("backend.extractor.extract_pdf")
    def test_dispatches_pdf(self, mock_extract):
        mock_extract.return_value = ExtractionResult(text="hello", page_count=1)
        result = extract_document("/some/file.pdf")
        assert result.text == "hello"
        mock_extract.assert_called_once_with("/some/file.pdf")

    @patch("backend.extractor.extract_docx")
    def test_dispatches_docx(self, mock_extract):
        mock_extract.return_value = ExtractionResult(text="world")
        result = extract_document("/some/file.docx")
        assert result.text == "world"
        mock_extract.assert_called_once_with("/some/file.docx")

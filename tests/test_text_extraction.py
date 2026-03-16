"""Tests for inline text extraction in get_attachment."""

import base64
import io
import zipfile
from unittest.mock import patch

import pytest

from microsoft_mcp.tools import (
    _extract_office_xml_text,
    _extract_text_content,
    _MAX_INLINE_CHARS,
)


class TestExtractTextContent:
    def test_extracts_text_plain(self):
        content = b"Hello, this is plain text."
        result = _extract_text_content(content, "text/plain")
        assert result == "Hello, this is plain text."

    def test_extracts_text_csv(self):
        content = b"name,age\nAlice,30\nBob,25"
        result = _extract_text_content(content, "text/csv")
        assert "Alice" in result
        assert "Bob" in result

    def test_extracts_text_html(self):
        content = b"<html><body><p>Hello</p></body></html>"
        result = _extract_text_content(content, "text/html")
        assert "<p>Hello</p>" in result

    def test_returns_none_for_binary(self):
        content = b"\x89PNG\r\n\x1a\n\x00\x00"
        result = _extract_text_content(content, "image/jpeg")
        assert result is None

    def test_returns_none_for_octet_stream(self):
        content = b"\x00\x01\x02\x03"
        result = _extract_text_content(content, "application/octet-stream")
        assert result is None

    def test_handles_utf8_errors_gracefully(self):
        content = b"Hello \xff\xfe world"
        result = _extract_text_content(content, "text/plain")
        assert result is not None
        assert "Hello" in result

    def test_returns_none_on_exception(self):
        """If extraction raises, returns None instead of propagating."""
        result = _extract_text_content(None, "text/plain")  # type: ignore[arg-type]
        assert result is None


class TestExtractOfficeXmlText:
    def _make_docx(self, text: str) -> bytes:
        """Create a minimal valid DOCX file with the given text."""
        buf = io.BytesIO()
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        doc_xml = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<w:document xmlns:w="{ns}">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>"
            f"</w:document>"
        )
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("word/document.xml", doc_xml)
        return buf.getvalue()

    def _make_xlsx(self, strings: list[str]) -> bytes:
        """Create a minimal valid XLSX with shared strings."""
        buf = io.BytesIO()
        ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        si_entries = "".join(f"<si><t>{s}</t></si>" for s in strings)
        ss_xml = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<sst xmlns="{ns}" count="{len(strings)}">{si_entries}</sst>'
        )
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("xl/sharedStrings.xml", ss_xml)
        return buf.getvalue()

    def test_extracts_docx_text(self):
        docx_bytes = self._make_docx("Hello from Word")
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        result = _extract_office_xml_text(docx_bytes, mime)
        assert result == "Hello from Word"

    def test_extracts_xlsx_text(self):
        xlsx_bytes = self._make_xlsx(["Revenue", "Expenses", "Profit"])
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        result = _extract_office_xml_text(xlsx_bytes, mime)
        assert "Revenue" in result
        assert "Expenses" in result

    def test_returns_none_for_bad_zip(self):
        result = _extract_office_xml_text(
            b"not a zip file",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        assert result is None

    def test_returns_none_for_unknown_mime(self):
        result = _extract_office_xml_text(b"anything", "application/pdf")
        assert result is None


class TestGetAttachmentInlineContent:
    """Test that get_attachment returns inline content for text-extractable types."""

    def _mock_graph_response(
        self, content: bytes, content_type: str, name: str = "test.txt"
    ):
        return {
            "name": name,
            "contentType": content_type,
            "size": len(content),
            "contentBytes": base64.b64encode(content).decode("ascii"),
        }

    @patch("microsoft_mcp.tools.graph")
    def test_text_file_includes_content_key(self, mock_graph, tmp_path):
        from microsoft_mcp.tools import get_attachment

        text = b"Meeting notes from Monday."
        mock_graph.request.return_value = self._mock_graph_response(
            text, "text/plain", "notes.txt"
        )

        save_path = str(tmp_path / "notes.txt")
        result = get_attachment("email1", "att1", save_path, "account1")

        assert "content" in result
        assert result["content"] == "Meeting notes from Monday."
        assert result["saved_to"] == save_path

    @patch("microsoft_mcp.tools.graph")
    def test_binary_file_omits_content_key(self, mock_graph, tmp_path):
        from microsoft_mcp.tools import get_attachment

        binary = b"\x89PNG\r\n\x1a\n\x00\x00\x00"
        mock_graph.request.return_value = self._mock_graph_response(
            binary, "image/png", "photo.png"
        )

        save_path = str(tmp_path / "photo.png")
        result = get_attachment("email1", "att1", save_path, "account1")

        assert "content" not in result
        assert result["saved_to"] == save_path

    @patch("microsoft_mcp.tools.graph")
    def test_truncates_large_text(self, mock_graph, tmp_path):
        from microsoft_mcp.tools import get_attachment

        large_text = b"x" * 60_000
        mock_graph.request.return_value = self._mock_graph_response(
            large_text, "text/plain", "big.txt"
        )

        save_path = str(tmp_path / "big.txt")
        result = get_attachment("email1", "att1", save_path, "account1")

        assert "content" in result
        assert "truncated" in result["content"].lower()
        # Content should be truncated to max + notice
        assert len(result["content"]) < 60_000

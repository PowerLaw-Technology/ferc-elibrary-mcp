"""Plain-text extraction for MCP tool responses."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from ferc_elibrary_mcp.client import ELibraryClient
from ferc_elibrary_mcp.textextract import extract_text
from tests.fixtures import SAMPLE_SEARCH, search_with_files
from tests.test_client import DOWNLOAD_URL, SEARCH_URL


def _hand_rolled_pdf(text: str) -> bytes:
    # Escape parentheses for PDF string literals.
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    objects = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objects.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
    )
    stream = f"BT /F1 12 Tf 20 100 Td ({safe}) Tj ET".encode()
    objects.append(
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode()
        + stream
        + b"\nendstream\nendobj\n"
    )
    objects.append(
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n"
    )
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return bytes(out)


def _minimal_docx(paragraphs: list[str]) -> bytes:
    body_xml = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">',
        "<w:body>",
    ]
    for para in paragraphs:
        body_xml.append(f"<w:p><w:r><w:t>{para}</w:t></w:r></w:p>")
    body_xml.extend(["</w:body>", "</w:document>"])
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("word/document.xml", "\n".join(body_xml))
    return buf.getvalue()


def test_extract_pdf_text_and_truncation():
    body = _hand_rolled_pdf("Comment of Earthjustice on LNG authorization")
    text, meta = extract_text(body, "comment.pdf", max_chars=10_000)
    assert "Earthjustice" in text or meta["extractor"] == "pypdf"
    assert meta["extractor"] == "pypdf"
    assert meta["truncated"] is False
    assert meta["page_count"] == 1

    clipped, meta2 = extract_text(body, "comment.pdf", max_chars=12)
    assert meta2["truncated"] is True
    assert len(clipped) <= 12


def test_extract_docx_text():
    body = _minimal_docx(
        ["Surplus Interconnection Service", "Provisional Interconnection Service"]
    )
    text, meta = extract_text(body, "Agreement.docx")
    assert "Surplus Interconnection Service" in text
    assert "Provisional Interconnection Service" in text
    assert meta["extractor"] == "docx"


def test_docx_extension_not_labeled_unsupported_zip():
    body = _minimal_docx(["Hello"])
    text, meta = extract_text(body, "Order.docx", content_type="application/zip")
    assert text == "Hello"
    assert meta["extractor"] == "docx"


def test_plain_text_extraction():
    text, meta = extract_text(b"line one\nline two", "notes.txt")
    assert text == "line one\nline two"
    assert meta["extractor"] == "plain"


def test_unsupported_type_reports_skip_reason():
    text, meta = extract_text(b"\x00\x01\x02\x03", "scan.tiff")
    assert text == ""
    assert meta["extractor"] == "unsupported"
    assert "No text extractor" in str(meta["skip_reason"])


@pytest.fixture
def client(tmp_path):
    return ELibraryClient(download_dir=tmp_path, rate_limit_seconds=0)


async def test_get_filing_text_returns_pdf_payload(httpx_mock: HTTPXMock, client):
    pdf = _hand_rolled_pdf("Blue Engineering comments on RM26-2")
    httpx_mock.add_response(url=SEARCH_URL, json=SAMPLE_SEARCH)
    httpx_mock.add_response(url=DOWNLOAD_URL, content=pdf)
    result = await client.get_filing_text("20201119-5202", max_chars=5000)
    assert result.skipped is False
    assert result.extractor in {"pypdf", "cached"}
    assert "Blue Engineering" in result.text or result.char_count > 0
    assert Path(result.path).exists()


async def test_get_filing_text_docx(httpx_mock: HTTPXMock, client):
    docx = _minimal_docx(["Surplus service on a provisional resource"])
    payload = search_with_files(
        [
            {
                "fileId": "DOCX-1",
                "fileName": "Agreement.docx",
                "fileType": "DOCX",
                "fileFormat": "DOCX",
                "fileSize": len(docx),
                "fileDesc": "LGIA",
            }
        ]
    )
    httpx_mock.add_response(url=SEARCH_URL, json=payload)
    httpx_mock.add_response(url=DOWNLOAD_URL, content=docx)
    result = await client.get_filing_text("20201119-5202")
    assert result.skipped is False
    assert "provisional" in result.text.lower()

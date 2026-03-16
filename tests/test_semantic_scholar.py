"""Tests for the Semantic Scholar fetcher."""
import os
from unittest.mock import MagicMock, patch

import pytest

from biolit.fetchers.semantic_scholar import fetch_s2_pdf, get_s2_pdf_url, _s2_headers


FAKE_PDF = b"%PDF-1.4 fake pdf content"
FAKE_PDF_URL = "https://arxiv.org/pdf/2021.12345.pdf"


def _mock_response(status_code: int, json_data: dict = None, content: bytes = b"", content_type: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"Content-Type": content_type}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    return resp


# ---------------------------------------------------------------------------
# _s2_headers
# ---------------------------------------------------------------------------

class TestS2Headers:
    def test_empty_when_no_key(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SEMANTIC_SCHOLAR_API_KEY", None)
            assert _s2_headers() == {}

    def test_includes_key_when_set(self):
        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "testkey123"}):
            headers = _s2_headers()
            assert headers.get("x-api-key") == "testkey123"


# ---------------------------------------------------------------------------
# get_s2_pdf_url
# ---------------------------------------------------------------------------

class TestGetS2PdfUrl:
    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_returns_url_when_present(self, mock_get):
        mock_get.return_value = _mock_response(200, json_data={
            "paperId": "abc123",
            "openAccessPdf": {"url": FAKE_PDF_URL, "status": "GREEN"},
        })
        assert get_s2_pdf_url("10.1038/s41588-021-00974-7") == FAKE_PDF_URL

    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_returns_none_on_404(self, mock_get):
        mock_get.return_value = _mock_response(404)
        assert get_s2_pdf_url("10.9999/nonexistent") is None

    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_returns_none_when_url_empty_string(self, mock_get):
        mock_get.return_value = _mock_response(200, json_data={
            "paperId": "abc123",
            "openAccessPdf": {"url": "", "status": None},
        })
        assert get_s2_pdf_url("10.64898/2026.03.05.709906") is None

    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_returns_none_when_open_access_pdf_null(self, mock_get):
        mock_get.return_value = _mock_response(200, json_data={
            "paperId": "abc123",
            "openAccessPdf": None,
        })
        assert get_s2_pdf_url("10.1038/s41588-021-00974-7") is None

    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")
        assert get_s2_pdf_url("10.1038/s41588-021-00974-7") is None

    def test_returns_none_for_empty_doi(self):
        assert get_s2_pdf_url("") is None
        assert get_s2_pdf_url(None) is None

    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_requests_correct_fields(self, mock_get):
        mock_get.return_value = _mock_response(200, json_data={"openAccessPdf": None})
        get_s2_pdf_url("10.1038/test")
        params = mock_get.call_args[1]["params"]
        assert params["fields"] == "openAccessPdf"

    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_url_includes_doi_prefix(self, mock_get):
        mock_get.return_value = _mock_response(200, json_data={"openAccessPdf": None})
        get_s2_pdf_url("10.1038/test")
        url = mock_get.call_args[0][0]
        assert "DOI:10.1038/test" in url


# ---------------------------------------------------------------------------
# fetch_s2_pdf
# ---------------------------------------------------------------------------

class TestFetchS2Pdf:
    @patch("biolit.fetchers.semantic_scholar.get_s2_pdf_url")
    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_returns_pdf_bytes_on_success(self, mock_get, mock_url):
        mock_url.return_value = FAKE_PDF_URL
        mock_get.return_value = _mock_response(200, content=FAKE_PDF, content_type="application/pdf")
        result = fetch_s2_pdf("10.1038/s41588-021-00974-7")
        assert result == FAKE_PDF

    @patch("biolit.fetchers.semantic_scholar.get_s2_pdf_url")
    def test_returns_none_when_no_url(self, mock_url):
        mock_url.return_value = None
        assert fetch_s2_pdf("10.9999/nonexistent") is None

    @patch("biolit.fetchers.semantic_scholar.get_s2_pdf_url")
    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_returns_none_when_response_not_pdf(self, mock_get, mock_url):
        mock_url.return_value = "https://example.com/landing"
        mock_get.return_value = _mock_response(200, content=b"<html>", content_type="text/html")
        assert fetch_s2_pdf("10.1038/s41588-021-00974-7") is None

    @patch("biolit.fetchers.semantic_scholar.get_s2_pdf_url")
    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_accepts_pdf_url_without_content_type(self, mock_get, mock_url):
        mock_url.return_value = "https://example.com/paper.pdf"
        mock_get.return_value = _mock_response(200, content=FAKE_PDF, content_type="")
        assert fetch_s2_pdf("10.1038/s41588-021-00974-7") == FAKE_PDF

    @patch("biolit.fetchers.semantic_scholar.get_s2_pdf_url")
    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_returns_none_on_download_error(self, mock_get, mock_url):
        mock_url.return_value = FAKE_PDF_URL
        mock_get.side_effect = ConnectionError("timeout")
        assert fetch_s2_pdf("10.1038/s41588-021-00974-7") is None

    def test_returns_none_for_empty_doi(self):
        assert fetch_s2_pdf("") is None
        assert fetch_s2_pdf(None) is None

"""Tests for the Semantic Scholar fetcher."""
import os
from unittest.mock import MagicMock, patch

import pytest

from biolit.fetchers.semantic_scholar import fetch_s2_pdf, get_s2_pdf_url, get_citation_count


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


# ---------------------------------------------------------------------------
# fetch_s2_pdf
# ---------------------------------------------------------------------------

class TestFetchS2Pdf:
    @patch("biolit.fetchers.semantic_scholar.get_s2_pdf_url")
    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_returns_pdf_bytes_on_success(self, mock_get, mock_url):
        mock_url.return_value = FAKE_PDF_URL
        mock_get.return_value = _mock_response(200, content=FAKE_PDF, content_type="application/pdf")
        assert fetch_s2_pdf("10.1038/s41588-021-00974-7") == FAKE_PDF

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
    def test_returns_none_on_download_error(self, mock_get, mock_url):
        mock_url.return_value = FAKE_PDF_URL
        mock_get.side_effect = ConnectionError("timeout")
        assert fetch_s2_pdf("10.1038/s41588-021-00974-7") is None

    def test_returns_none_for_empty_doi(self):
        assert fetch_s2_pdf("") is None
        assert fetch_s2_pdf(None) is None


# ---------------------------------------------------------------------------
# get_citation_count
# ---------------------------------------------------------------------------

class TestGetCitationCount:
    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_returns_count_via_pmid(self, mock_get):
        mock_get.return_value = _mock_response(200, json_data={"paperId": "abc", "citationCount": 42})
        assert get_citation_count(pmid="12345678") == 42

    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_falls_back_to_doi_when_pmid_not_found(self, mock_get):
        not_found = _mock_response(404)
        found = _mock_response(200, json_data={"citationCount": 13})
        mock_get.side_effect = [not_found, found]
        assert get_citation_count(doi="10.1038/test", pmid="99999999") == 13

    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_returns_none_when_not_found(self, mock_get):
        mock_get.return_value = _mock_response(404)
        assert get_citation_count(doi="10.9999/nonexistent") is None

    @patch("biolit.fetchers.semantic_scholar.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")
        assert get_citation_count(pmid="12345678") is None

    def test_returns_none_with_no_identifiers(self):
        assert get_citation_count() is None

"""Tests for the OpenAlex open-access PDF fetcher."""
from unittest.mock import MagicMock, patch

from biolit.fetchers.openalex import fetch_via_openalex, _candidate_pdf_urls


FAKE_PDF = b"%PDF-1.5 fake openalex manuscript"
PDF_URL = "https://repository.example.edu/manuscript.pdf"


def _mock_response(status_code: int, json_data: dict = None, content: bytes = b"") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    return resp


class TestCandidatePdfUrls:
    def test_orders_best_then_primary_then_locations(self):
        work = {
            "best_oa_location": {"pdf_url": "best.pdf"},
            "primary_location": {"pdf_url": "primary.pdf"},
            "locations": [{"pdf_url": "loc1.pdf"}, {"pdf_url": "loc2.pdf"}],
        }
        assert _candidate_pdf_urls(work) == ["best.pdf", "primary.pdf", "loc1.pdf", "loc2.pdf"]

    def test_dedupes_and_skips_null(self):
        work = {
            "best_oa_location": {"pdf_url": "x.pdf"},
            "primary_location": {"pdf_url": None},
            "locations": [{"pdf_url": "x.pdf"}, {"landing_page_url": "y"}],
        }
        assert _candidate_pdf_urls(work) == ["x.pdf"]

    def test_empty_when_no_pdfs(self):
        assert _candidate_pdf_urls({"best_oa_location": None, "locations": []}) == []


class TestFetchViaOpenAlex:
    @patch("biolit.fetchers.openalex.requests.get")
    def test_returns_pdf_bytes_from_best_oa_location(self, mock_get):
        meta = _mock_response(200, json_data={"best_oa_location": {"pdf_url": PDF_URL}})
        pdf = _mock_response(200, content=FAKE_PDF)
        mock_get.side_effect = [meta, pdf]
        assert fetch_via_openalex("10.1/x", mailto="me@example.org") == FAKE_PDF

    @patch("biolit.fetchers.openalex.requests.get")
    def test_falls_back_to_later_location_when_first_not_pdf(self, mock_get):
        meta = _mock_response(200, json_data={
            "best_oa_location": {"pdf_url": "landing.pdf"},
            "locations": [{"pdf_url": PDF_URL}],
        })
        html = _mock_response(200, content=b"<html>not a pdf</html>")
        pdf = _mock_response(200, content=FAKE_PDF)
        mock_get.side_effect = [meta, html, pdf]
        assert fetch_via_openalex("10.1/x") == FAKE_PDF

    @patch("biolit.fetchers.openalex.requests.get")
    def test_returns_none_when_no_pdf_url(self, mock_get):
        mock_get.return_value = _mock_response(200, json_data={"best_oa_location": None, "locations": []})
        assert fetch_via_openalex("10.1/x") is None

    @patch("biolit.fetchers.openalex.requests.get")
    def test_returns_none_on_404(self, mock_get):
        mock_get.return_value = _mock_response(404)
        assert fetch_via_openalex("10.9999/missing") is None

    @patch("biolit.fetchers.openalex.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")
        assert fetch_via_openalex("10.1/x") is None

    def test_returns_none_for_empty_doi(self):
        assert fetch_via_openalex("") is None
        assert fetch_via_openalex(None) is None

"""Tests for the Europe PMC open-access full-text PDF fetcher."""
from unittest.mock import MagicMock, patch

from biolit.fetchers.europepmc_pdf import fetch_europepmc_pdf


FAKE_PDF = b"%PDF-1.7 europe pmc oa fulltext"


def _mock_response(status_code: int, content: bytes = b"") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


class TestFetchEuropePmcPdf:
    @patch("biolit.fetchers.europepmc_pdf.requests.get")
    def test_returns_pdf_bytes_for_pmcid(self, mock_get):
        mock_get.return_value = _mock_response(200, content=FAKE_PDF)
        assert fetch_europepmc_pdf(pmcid="PMC123456") == FAKE_PDF

    @patch("biolit.fetchers.europepmc_pdf.requests.get")
    def test_normalizes_bare_numeric_pmcid(self, mock_get):
        mock_get.return_value = _mock_response(200, content=FAKE_PDF)
        fetch_europepmc_pdf(pmcid="123456")
        url = mock_get.call_args[0][0]
        assert "PMC123456/fullTextPDF" in url

    @patch("biolit.fetchers.europepmc_pdf.requests.get")
    def test_resolves_pmid_to_pmcid(self, mock_get):
        mock_get.return_value = _mock_response(200, content=FAKE_PDF)
        with patch("biolit.fetchers.pubmed.pmid_to_pmcid", return_value="PMC777") as mock_conv:
            assert fetch_europepmc_pdf(pmid="999") == FAKE_PDF
            mock_conv.assert_called_once_with("999")

    @patch("biolit.fetchers.europepmc_pdf.requests.get")
    def test_resolves_doi_to_pmcid(self, mock_get):
        mock_get.return_value = _mock_response(200, content=FAKE_PDF)
        with patch("biolit.fetchers.pubmed.pmid_to_pmcid", return_value=None), \
             patch("biolit.fetchers.pubmed.doi_to_pmcid", return_value="PMC888") as mock_doi:
            assert fetch_europepmc_pdf(doi="10.1/x") == FAKE_PDF
            mock_doi.assert_called_once_with("10.1/x")

    def test_returns_none_when_no_pmcid_resolvable(self):
        with patch("biolit.fetchers.pubmed.pmid_to_pmcid", return_value=None), \
             patch("biolit.fetchers.pubmed.doi_to_pmcid", return_value=None):
            assert fetch_europepmc_pdf(pmid="1", doi="10.1/x") is None

    @patch("biolit.fetchers.europepmc_pdf.requests.get")
    def test_returns_none_when_not_pdf(self, mock_get):
        mock_get.return_value = _mock_response(200, content=b"<html>landing</html>")
        assert fetch_europepmc_pdf(pmcid="PMC123456") is None

    @patch("biolit.fetchers.europepmc_pdf.requests.get")
    def test_returns_none_on_404(self, mock_get):
        mock_get.return_value = _mock_response(404)
        assert fetch_europepmc_pdf(pmcid="PMC123456") is None

    @patch("biolit.fetchers.europepmc_pdf.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")
        assert fetch_europepmc_pdf(pmcid="PMC123456") is None

    def test_returns_none_with_no_identifiers(self):
        assert fetch_europepmc_pdf() is None

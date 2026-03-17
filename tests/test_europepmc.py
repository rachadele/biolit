"""Tests for the Europe PMC fetcher and related pubmed ID-converter helpers."""
from unittest.mock import MagicMock, patch

import pytest

from biolit.fetchers.europepmc import fetch_europepmc_fulltext, _get_fulltext_xml
from biolit.fetchers.pubmed import doi_to_pmcid, doi_to_pmid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code: int, content: bytes = b"") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


FAKE_JATS = b"<article><body><p>Full text.</p></body></article>"


# ---------------------------------------------------------------------------
# _get_fulltext_xml
# ---------------------------------------------------------------------------

class TestGetFulltextXml:
    @patch("biolit.fetchers.europepmc.requests.get")
    def test_returns_content_on_200(self, mock_get):
        mock_get.return_value = _mock_response(200, FAKE_JATS)
        result = _get_fulltext_xml("MED", "12345678")
        assert result == FAKE_JATS

    @patch("biolit.fetchers.europepmc.requests.get")
    def test_returns_none_on_404(self, mock_get):
        mock_get.return_value = _mock_response(404)
        assert _get_fulltext_xml("MED", "12345678") is None

    @patch("biolit.fetchers.europepmc.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")
        assert _get_fulltext_xml("MED", "12345678") is None


# ---------------------------------------------------------------------------
# fetch_europepmc_fulltext — pmid path
# ---------------------------------------------------------------------------

class TestFetchEuropepmcFulltextByPmid:
    @patch("biolit.fetchers.europepmc.requests.get")
    def test_returns_xml_when_found(self, mock_get):
        mock_get.return_value = _mock_response(200, FAKE_JATS)
        assert fetch_europepmc_fulltext(pmid="12345678") == FAKE_JATS

    @patch("biolit.fetchers.europepmc.requests.get")
    def test_returns_none_when_not_found(self, mock_get):
        mock_get.return_value = _mock_response(404)
        assert fetch_europepmc_fulltext(pmid="12345678") is None


# ---------------------------------------------------------------------------
# fetch_europepmc_fulltext — DOI fallback path
# ---------------------------------------------------------------------------

class TestFetchEuropepmcFulltextByDoi:
    # Note: doi_to_pmid/doi_to_pmcid are patched directly (not via requests.get)
    # because both europepmc.py and pubmed.py do `import requests`, meaning they
    # share the same requests.get object — patching both simultaneously conflicts.

    @patch("biolit.fetchers.europepmc.requests.get")
    @patch("biolit.fetchers.pubmed.doi_to_pmcid")
    @patch("biolit.fetchers.pubmed.doi_to_pmid")
    def test_doi_resolves_to_pmid_and_fetches(self, mock_doi_to_pmid, mock_doi_to_pmcid, mock_get):
        mock_doi_to_pmid.return_value = "12345678"
        mock_doi_to_pmcid.return_value = None
        mock_get.return_value = _mock_response(200, FAKE_JATS)

        result = fetch_europepmc_fulltext(doi="10.1038/s41588-026-01234-5")
        assert result == FAKE_JATS
        assert "MED/12345678/fullTextXML" in mock_get.call_args[0][0]

    @patch("biolit.fetchers.europepmc.requests.get")
    @patch("biolit.fetchers.pubmed.doi_to_pmcid")
    @patch("biolit.fetchers.pubmed.doi_to_pmid")
    def test_doi_resolves_to_pmcid_when_no_pmid(self, mock_doi_to_pmid, mock_doi_to_pmcid, mock_get):
        mock_doi_to_pmid.return_value = None
        mock_doi_to_pmcid.return_value = "PMC9984800"
        mock_get.return_value = _mock_response(200, FAKE_JATS)

        result = fetch_europepmc_fulltext(doi="10.1038/s41588-026-01234-5")
        assert result == FAKE_JATS
        assert "PMC/9984800/fullTextXML" in mock_get.call_args[0][0]

    @patch("biolit.fetchers.europepmc.requests.get")
    @patch("biolit.fetchers.pubmed.doi_to_pmcid")
    @patch("biolit.fetchers.pubmed.doi_to_pmid")
    def test_returns_none_when_doi_cannot_be_resolved(self, mock_doi_to_pmid, mock_doi_to_pmcid, mock_get):
        mock_doi_to_pmid.return_value = None
        mock_doi_to_pmcid.return_value = None
        assert fetch_europepmc_fulltext(doi="10.9999/nonexistent") is None

    def test_returns_none_with_no_arguments(self):
        assert fetch_europepmc_fulltext() is None


# ---------------------------------------------------------------------------
# doi_to_pmcid
# ---------------------------------------------------------------------------

class TestDoiToPmcid:
    @patch("biolit.fetchers.pubmed.requests.get")
    def test_returns_pmcid(self, mock_get):
        mock_get.return_value = MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"records": [{"pmcid": "PMC9984800"}]}),
        )
        assert doi_to_pmcid("10.1038/s41588-026-01234-5") == "PMC9984800"

    @patch("biolit.fetchers.pubmed.requests.get")
    def test_returns_none_when_no_pmcid(self, mock_get):
        mock_get.return_value = MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"records": [{"pmid": "12345678"}]}),
        )
        assert doi_to_pmcid("10.1038/s41588-026-01234-5") is None


# ---------------------------------------------------------------------------
# doi_to_pmid
# ---------------------------------------------------------------------------

class TestDoiToPmid:
    @patch("biolit.fetchers.pubmed.requests.get")
    def test_returns_pmid_as_string(self, mock_get):
        mock_get.return_value = MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"esearchresult": {"idlist": ["12345678"]}}),
        )
        result = doi_to_pmid("10.1038/s41588-026-01234-5")
        assert result == "12345678"
        assert isinstance(result, str)

    @patch("biolit.fetchers.pubmed.requests.get")
    def test_returns_none_when_no_results(self, mock_get):
        mock_get.return_value = MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"esearchresult": {"idlist": []}}),
        )
        assert doi_to_pmid("10.9999/nonexistent") is None

    @patch("biolit.fetchers.pubmed.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")
        assert doi_to_pmid("10.1038/s41588-026-01234-5") is None


# ---------------------------------------------------------------------------
# resolve_fulltext — Europe PMC integration
# ---------------------------------------------------------------------------

class TestResolveFulltextEuropePmc:
    """Verify Europe PMC slots correctly into the resolve_fulltext chain."""

    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_pmc_fulltext")
    def test_europepmc_used_when_pmc_fails(
        self, mock_pmc, mock_epmc, sample_pubmed_metadata, sample_jats_xml
    ):
        from biolit.pipeline import resolve_fulltext

        mock_pmc.return_value = None
        mock_epmc.return_value = sample_jats_xml

        text, source, _ = resolve_fulltext(sample_pubmed_metadata)
        assert source == "europepmc_fulltext"
        assert len(text) > 0

    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_pmc_fulltext")
    def test_pmc_takes_priority_over_europepmc(
        self, mock_pmc, mock_epmc, sample_pubmed_metadata, sample_jats_xml
    ):
        from biolit.pipeline import resolve_fulltext

        mock_pmc.return_value = sample_jats_xml
        mock_epmc.return_value = sample_jats_xml

        _, source, _ = resolve_fulltext(sample_pubmed_metadata)
        assert source == "pmc_fulltext"
        mock_epmc.assert_not_called()

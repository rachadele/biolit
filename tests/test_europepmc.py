"""Tests for the Europe PMC fetcher and related pubmed ID-converter helpers."""
from unittest.mock import MagicMock, patch

import pytest

from biolit.fetchers.europepmc import fetch_europepmc_fulltext, _get_fulltext_xml
from biolit.fetchers.pubmed import doi_to_pmcid, doi_to_pmid, _idconv_lookup


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
        mock_get.assert_called_once()
        assert "MED/12345678/fullTextXML" in mock_get.call_args[0][0]

    @patch("biolit.fetchers.europepmc.requests.get")
    def test_returns_none_on_404(self, mock_get):
        mock_get.return_value = _mock_response(404)
        assert _get_fulltext_xml("MED", "12345678") is None

    @patch("biolit.fetchers.europepmc.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")
        assert _get_fulltext_xml("MED", "12345678") is None

    @patch("biolit.fetchers.europepmc.requests.get")
    def test_uses_correct_url_for_pmc_source(self, mock_get):
        mock_get.return_value = _mock_response(200, FAKE_JATS)
        _get_fulltext_xml("PMC", "9984800")
        url = mock_get.call_args[0][0]
        assert "PMC/9984800/fullTextXML" in url


# ---------------------------------------------------------------------------
# fetch_europepmc_fulltext — pmid path
# ---------------------------------------------------------------------------

class TestFetchEuropepmcFulltextByPmid:
    @patch("biolit.fetchers.europepmc.requests.get")
    def test_returns_xml_when_med_succeeds(self, mock_get):
        mock_get.return_value = _mock_response(200, FAKE_JATS)
        result = fetch_europepmc_fulltext(pmid="12345678")
        assert result == FAKE_JATS

    @patch("biolit.fetchers.europepmc.requests.get")
    def test_returns_none_when_med_404(self, mock_get):
        mock_get.return_value = _mock_response(404)
        assert fetch_europepmc_fulltext(pmid="12345678") is None

    @patch("biolit.fetchers.europepmc.requests.get")
    def test_only_one_request_when_pmid_succeeds(self, mock_get):
        mock_get.return_value = _mock_response(200, FAKE_JATS)
        fetch_europepmc_fulltext(pmid="12345678")
        assert mock_get.call_count == 1


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
        url = mock_get.call_args[0][0]
        assert "MED/12345678/fullTextXML" in url

    @patch("biolit.fetchers.europepmc.requests.get")
    @patch("biolit.fetchers.pubmed.doi_to_pmcid")
    @patch("biolit.fetchers.pubmed.doi_to_pmid")
    def test_doi_resolves_to_pmcid_when_no_pmid(self, mock_doi_to_pmid, mock_doi_to_pmcid, mock_get):
        mock_doi_to_pmid.return_value = None
        mock_doi_to_pmcid.return_value = "PMC9984800"
        mock_get.return_value = _mock_response(200, FAKE_JATS)

        result = fetch_europepmc_fulltext(doi="10.1038/s41588-026-01234-5")
        assert result == FAKE_JATS
        url = mock_get.call_args[0][0]
        assert "PMC/9984800/fullTextXML" in url

    @patch("biolit.fetchers.europepmc.requests.get")
    @patch("biolit.fetchers.pubmed.doi_to_pmid")
    def test_doi_path_not_taken_when_pmid_provided(self, mock_doi_to_pmid, mock_get):
        """When pmid is given, the DOI branch must not be entered."""
        mock_get.return_value = _mock_response(200, FAKE_JATS)
        fetch_europepmc_fulltext(pmid="12345678", doi="10.1038/s41588-026-01234-5")
        mock_doi_to_pmid.assert_not_called()

    @patch("biolit.fetchers.europepmc.requests.get")
    @patch("biolit.fetchers.pubmed.doi_to_pmcid")
    @patch("biolit.fetchers.pubmed.doi_to_pmid")
    def test_returns_none_when_doi_cannot_be_resolved(self, mock_doi_to_pmid, mock_doi_to_pmcid, mock_get):
        mock_doi_to_pmid.return_value = None
        mock_doi_to_pmcid.return_value = None

        assert fetch_europepmc_fulltext(doi="10.9999/nonexistent") is None
        mock_get.assert_not_called()

    def test_returns_none_with_no_arguments(self):
        assert fetch_europepmc_fulltext() is None


# ---------------------------------------------------------------------------
# NCBI ID Converter helpers (doi_to_pmcid, doi_to_pmid, _idconv_lookup)
# ---------------------------------------------------------------------------

class TestIdconvLookup:
    @patch("biolit.fetchers.pubmed.requests.get")
    def test_returns_first_record(self, mock_get):
        mock_get.return_value = MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"records": [{"pmid": "111", "pmcid": "PMC222"}]}),
        )
        rec = _idconv_lookup("10.1234/test")
        assert rec == {"pmid": "111", "pmcid": "PMC222"}

    @patch("biolit.fetchers.pubmed.requests.get")
    def test_returns_empty_dict_on_empty_records(self, mock_get):
        mock_get.return_value = MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"records": []}),
        )
        assert _idconv_lookup("10.1234/test") == {}

    @patch("biolit.fetchers.pubmed.requests.get")
    def test_returns_empty_dict_on_network_error(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")
        assert _idconv_lookup("anything") == {}


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


class TestDoiToPmid:
    @patch("biolit.fetchers.pubmed.requests.get")
    def test_returns_pmid_as_string(self, mock_get):
        mock_get.return_value = MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"records": [{"pmid": 12345678}]}),
        )
        result = doi_to_pmid("10.1038/s41588-026-01234-5")
        assert result == "12345678"
        assert isinstance(result, str)

    @patch("biolit.fetchers.pubmed.requests.get")
    def test_returns_none_when_no_pmid(self, mock_get):
        mock_get.return_value = MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"records": [{"pmcid": "PMC9984800"}]}),
        )
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

        mock_pmc.return_value = None          # PMC efetch misses
        mock_epmc.return_value = sample_jats_xml  # Europe PMC hits

        text, source, _ = resolve_fulltext(sample_pubmed_metadata)
        assert source == "europepmc_fulltext"
        assert len(text) > 0

    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_pmc_fulltext")
    def test_pmc_takes_priority_over_europepmc(
        self, mock_pmc, mock_epmc, sample_pubmed_metadata, sample_jats_xml
    ):
        from biolit.pipeline import resolve_fulltext

        mock_pmc.return_value = sample_jats_xml  # PMC efetch hits
        mock_epmc.return_value = sample_jats_xml

        _, source, _ = resolve_fulltext(sample_pubmed_metadata)
        assert source == "pmc_fulltext"
        mock_epmc.assert_not_called()

    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_pmc_fulltext")
    def test_europepmc_receives_doi(
        self, mock_pmc, mock_epmc, sample_pubmed_metadata
    ):
        from biolit.pipeline import resolve_fulltext

        mock_pmc.return_value = None
        mock_epmc.return_value = None

        resolve_fulltext(sample_pubmed_metadata)
        _, kwargs = mock_epmc.call_args
        assert kwargs.get("doi") == sample_pubmed_metadata["doi"]

    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_pmc_fulltext")
    def test_europepmc_xml_saved_to_artifacts(
        self, mock_pmc, mock_epmc, sample_pubmed_metadata, sample_jats_xml
    ):
        from biolit.pipeline import resolve_fulltext

        mock_pmc.return_value = None
        mock_epmc.return_value = sample_jats_xml

        _, _, artifacts = resolve_fulltext(sample_pubmed_metadata)
        assert "europepmc_xml" in artifacts
        assert artifacts["europepmc_xml"] == sample_jats_xml

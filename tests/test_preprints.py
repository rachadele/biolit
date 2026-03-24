"""Tests for the preprint fetcher (bioRxiv / medRxiv)."""
from unittest.mock import MagicMock, patch

import pytest

from biolit.fetchers.preprints import _is_preprint_doi, fetch_preprint, fetch_preprint_metadata


FAKE_JATS = b"<article><body><p>Preprint full text.</p></body></article>"
FAKE_JATS_URL = "https://www.biorxiv.org/content/early/2026/03/07/2026.03.05.709906.source.xml"

BIORXIV_API_RECORD = {
    "title": "LLM-assisted curation of genomics metadata",
    "abstract": "We evaluated GPT-4o for annotation of cell lines.",
    "doi": "10.64898/2026.03.05.709906",
    "jatsxml": FAKE_JATS_URL,
    "version": "1",
    "server": "bioRxiv",
    "authors": "Smith J; Jones A; Brown B",
}


def _api_resp(collection: list) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"collection": collection}
    return resp


def _jats_resp(status_code: int, content: bytes = b"") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


# ---------------------------------------------------------------------------
# _is_preprint_doi
# ---------------------------------------------------------------------------

class TestIsPreprintDoi:
    def test_legacy_biorxiv_prefix(self):
        assert _is_preprint_doi("10.1101/2021.01.01.425001") is True

    def test_new_biorxiv_prefix(self):
        assert _is_preprint_doi("10.64898/2026.03.05.709906") is True

    def test_published_journal_doi(self):
        assert _is_preprint_doi("10.1038/s41588-021-00974-7") is False

    def test_empty_string(self):
        assert _is_preprint_doi("") is False

    def test_none(self):
        assert _is_preprint_doi(None) is False


# ---------------------------------------------------------------------------
# fetch_preprint
# ---------------------------------------------------------------------------

class TestFetchPreprint:
    @patch("biolit.fetchers.preprints.requests.get")
    def test_returns_xml_on_success(self, mock_get):
        mock_get.side_effect = [
            _api_resp([BIORXIV_API_RECORD]),   # API call
            _jats_resp(200, FAKE_JATS),        # JATS XML download
        ]
        result = fetch_preprint("10.64898/2026.03.05.709906")
        assert result == FAKE_JATS

    @patch("biolit.fetchers.preprints.requests.get")
    def test_returns_none_for_non_preprint_doi(self, mock_get):
        result = fetch_preprint("10.1038/s41588-021-00974-7")
        assert result is None
        mock_get.assert_not_called()

    @patch("biolit.fetchers.preprints.requests.get")
    def test_returns_none_when_collection_empty(self, mock_get):
        mock_get.return_value = _api_resp([])
        assert fetch_preprint("10.1101/2021.01.01.425001") is None

    @patch("biolit.fetchers.preprints.requests.get")
    def test_returns_none_on_403_cloudflare(self, mock_get):
        mock_get.side_effect = [
            _api_resp([BIORXIV_API_RECORD]),
            _jats_resp(403),
        ]
        assert fetch_preprint("10.64898/2026.03.05.709906") is None

    @patch("biolit.fetchers.preprints.requests.get")
    def test_uses_jatsxml_url_from_api_response(self, mock_get):
        mock_get.side_effect = [
            _api_resp([BIORXIV_API_RECORD]),
            _jats_resp(200, FAKE_JATS),
        ]
        fetch_preprint("10.64898/2026.03.05.709906")
        # Second call should be to the jatsxml URL from the API record
        jats_call_url = mock_get.call_args_list[1][0][0]
        assert jats_call_url == FAKE_JATS_URL

    @patch("biolit.fetchers.preprints.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")
        assert fetch_preprint("10.1101/2021.01.01.425001") is None


# ---------------------------------------------------------------------------
# fetch_preprint_metadata
# ---------------------------------------------------------------------------

class TestFetchPreprintMetadata:
    @patch("biolit.fetchers.preprints.requests.get")
    def test_returns_metadata_dict(self, mock_get):
        mock_get.return_value = _api_resp([BIORXIV_API_RECORD])
        result = fetch_preprint_metadata("10.64898/2026.03.05.709906")
        assert result is not None
        assert result["title"] == BIORXIV_API_RECORD["title"]
        assert result["abstract"] == BIORXIV_API_RECORD["abstract"]
        assert result["doi"] == "10.64898/2026.03.05.709906"
        assert result["server"] == "biorxiv"

    @patch("biolit.fetchers.preprints.requests.get")
    def test_returns_none_for_non_preprint_doi(self, mock_get):
        result = fetch_preprint_metadata("10.1038/s41588-021-00974-7")
        assert result is None
        mock_get.assert_not_called()

    @patch("biolit.fetchers.preprints.requests.get")
    def test_returns_none_when_collection_empty(self, mock_get):
        mock_get.return_value = _api_resp([])
        assert fetch_preprint_metadata("10.1101/2021.01.01.425001") is None

    @patch("biolit.fetchers.preprints.requests.get")
    def test_falls_back_to_doi_as_title(self, mock_get):
        record_no_title = {k: v for k, v in BIORXIV_API_RECORD.items() if k != "title"}
        mock_get.return_value = _api_resp([record_no_title])
        result = fetch_preprint_metadata("10.64898/2026.03.05.709906")
        assert result["title"] == "10.64898/2026.03.05.709906"

    @patch("biolit.fetchers.preprints.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")
        assert fetch_preprint_metadata("10.1101/2021.01.01.425001") is None

    @patch("biolit.fetchers.preprints.requests.get")
    def test_authors_passed_through(self, mock_get):
        mock_get.return_value = _api_resp([BIORXIV_API_RECORD])
        result = fetch_preprint_metadata("10.64898/2026.03.05.709906")
        assert result["authors"] == "Smith J; Jones A; Brown B"

    @patch("biolit.fetchers.preprints.requests.get")
    def test_authors_none_when_absent(self, mock_get):
        record_no_authors = {k: v for k, v in BIORXIV_API_RECORD.items() if k != "authors"}
        mock_get.return_value = _api_resp([record_no_authors])
        result = fetch_preprint_metadata("10.64898/2026.03.05.709906")
        assert result["authors"] is None

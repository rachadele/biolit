"""Tests for the CORE open-access PDF fetcher (opt-in via CORE_API_KEY)."""
from unittest.mock import MagicMock, patch

from biolit.fetchers.core import fetch_via_core


FAKE_PDF = b"%PDF-1.4 core repository copy"
DOWNLOAD_URL = "https://core.ac.uk/download/123.pdf"


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


class TestFetchViaCore:
    def test_noop_without_api_key(self, monkeypatch):
        monkeypatch.delenv("CORE_API_KEY", raising=False)
        # Must not touch the network when no key is configured.
        with patch("biolit.fetchers.core.requests.post") as mock_post:
            assert fetch_via_core("10.1/x") is None
            mock_post.assert_not_called()

    @patch("biolit.fetchers.core.requests.get")
    @patch("biolit.fetchers.core.requests.post")
    def test_returns_pdf_bytes_on_success(self, mock_post, mock_get):
        mock_post.return_value = _mock_response(200, json_data={"results": [{"downloadUrl": DOWNLOAD_URL}]})
        mock_get.return_value = _mock_response(200, content=FAKE_PDF)
        assert fetch_via_core("10.1/x", api_key="KEY") == FAKE_PDF

    @patch("biolit.fetchers.core.requests.post")
    def test_returns_none_when_no_results(self, mock_post):
        mock_post.return_value = _mock_response(200, json_data={"results": []})
        assert fetch_via_core("10.1/x", api_key="KEY") is None

    @patch("biolit.fetchers.core.requests.post")
    def test_returns_none_when_no_download_url(self, mock_post):
        mock_post.return_value = _mock_response(200, json_data={"results": [{"id": 1}]})
        assert fetch_via_core("10.1/x", api_key="KEY") is None

    @patch("biolit.fetchers.core.requests.get")
    @patch("biolit.fetchers.core.requests.post")
    def test_returns_none_when_not_pdf(self, mock_post, mock_get):
        mock_post.return_value = _mock_response(200, json_data={"results": [{"downloadUrl": DOWNLOAD_URL}]})
        mock_get.return_value = _mock_response(200, content=b"<html>")
        assert fetch_via_core("10.1/x", api_key="KEY") is None

    @patch("biolit.fetchers.core.requests.post")
    def test_returns_none_on_network_error(self, mock_post):
        mock_post.side_effect = ConnectionError("timeout")
        assert fetch_via_core("10.1/x", api_key="KEY") is None

    def test_returns_none_for_empty_doi(self):
        assert fetch_via_core("", api_key="KEY") is None
        assert fetch_via_core(None, api_key="KEY") is None

    @patch("biolit.fetchers.core.requests.get")
    @patch("biolit.fetchers.core.requests.post")
    def test_uses_env_var_key(self, mock_post, mock_get, monkeypatch):
        monkeypatch.setenv("CORE_API_KEY", "ENVKEY")
        mock_post.return_value = _mock_response(200, json_data={"results": [{"downloadUrl": DOWNLOAD_URL}]})
        mock_get.return_value = _mock_response(200, content=FAKE_PDF)
        assert fetch_via_core("10.1/x") == FAKE_PDF
        # Authorization header carries the env key.
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer ENVKEY"

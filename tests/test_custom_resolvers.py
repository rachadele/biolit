"""Tests for the user-configurable custom-resolver PDF fetcher."""
import json
from unittest.mock import MagicMock, patch

from biolit.fetchers.custom_resolvers import (
    fetch_via_custom_resolvers,
    _fill_template,
    _load_resolvers,
)


FAKE_PDF = b"%PDF-1.4 proxied copy"


def _mock_response(status_code: int, content: bytes = b"", url: str = "https://proxy.example.edu/x") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.url = url
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestLoadResolvers:
    def test_arg_takes_precedence(self):
        assert _load_resolvers([{"url_template": "x"}]) == [{"url_template": "x"}]

    def test_env_json_array(self, monkeypatch):
        monkeypatch.setenv("BIOLIT_CUSTOM_RESOLVERS", '[{"url_template": "https://x/{doi}"}]')
        assert _load_resolvers(None) == [{"url_template": "https://x/{doi}"}]

    def test_env_single_object_wrapped(self, monkeypatch):
        monkeypatch.setenv("BIOLIT_CUSTOM_RESOLVERS", '{"url_template": "https://x/{doi}"}')
        assert _load_resolvers(None) == [{"url_template": "https://x/{doi}"}]

    def test_empty_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("BIOLIT_CUSTOM_RESOLVERS", raising=False)
        assert _load_resolvers(None) == []

    def test_malformed_json_yields_empty(self, monkeypatch):
        monkeypatch.setenv("BIOLIT_CUSTOM_RESOLVERS", "not json{{")
        assert _load_resolvers(None) == []


# ---------------------------------------------------------------------------
# Template filling
# ---------------------------------------------------------------------------

class TestFillTemplate:
    def test_doi_substitution(self):
        assert _fill_template("https://x/{doi}", "10.1/a", None) == "https://x/10.1/a"

    def test_doi_encoded(self):
        assert _fill_template("https://x?id={doi_encoded}", "10.1/a b", None) == "https://x?id=10.1%2Fa%20b"

    def test_url_substitution(self):
        assert _fill_template("https://p/?u={url}", None, "https://d/x") == "https://p/?u=https://d/x"

    def test_none_when_required_value_missing(self):
        assert _fill_template("https://x/{doi}", None, "https://d/x") is None
        assert _fill_template("https://x/{url}", "10.1/a", None) is None


# ---------------------------------------------------------------------------
# End-to-end fetch
# ---------------------------------------------------------------------------

class TestFetchViaCustomResolvers:
    def test_noop_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("BIOLIT_CUSTOM_RESOLVERS", raising=False)
        with patch("biolit.fetchers.custom_resolvers.requests.get") as mock_get:
            assert fetch_via_custom_resolvers(doi="10.1/a") is None
            mock_get.assert_not_called()

    @patch("biolit.fetchers.custom_resolvers.requests.get")
    def test_hits_templated_url_and_returns_pdf(self, mock_get):
        mock_get.return_value = _mock_response(200, content=FAKE_PDF)
        resolvers = [{"url_template": "https://proxy.example.edu/login?url=https://doi.org/{doi}"}]
        assert fetch_via_custom_resolvers(doi="10.1/a", resolvers=resolvers) == FAKE_PDF
        assert mock_get.call_args.args[0] == "https://proxy.example.edu/login?url=https://doi.org/10.1/a"

    @patch("biolit.fetchers.custom_resolvers.requests.get")
    def test_env_config_drives_request(self, mock_get, monkeypatch):
        monkeypatch.setenv(
            "BIOLIT_CUSTOM_RESOLVERS",
            json.dumps([{"url_template": "https://r/openurl?id={doi_encoded}"}]),
        )
        mock_get.return_value = _mock_response(200, content=FAKE_PDF)
        assert fetch_via_custom_resolvers(doi="10.1/a") == FAKE_PDF

    @patch("biolit.fetchers.custom_resolvers.requests.get")
    def test_tries_next_resolver_when_first_not_pdf(self, mock_get):
        mock_get.side_effect = [
            _mock_response(200, content=b"<html>paywall</html>"),
            _mock_response(200, content=FAKE_PDF),
        ]
        resolvers = [
            {"url_template": "https://a/{doi}"},
            {"url_template": "https://b/{doi}"},
        ]
        assert fetch_via_custom_resolvers(doi="10.1/a", resolvers=resolvers) == FAKE_PDF

    @patch("biolit.fetchers.custom_resolvers.requests.get")
    def test_extra_headers_and_user_agent_sent(self, mock_get):
        mock_get.return_value = _mock_response(200, content=FAKE_PDF)
        resolvers = [{
            "url_template": "https://a/{doi}",
            "user_agent": "proxy-agent/1.0",
            "headers": {"Cookie": "ezproxy=SESSION"},
        }]
        fetch_via_custom_resolvers(doi="10.1/a", resolvers=resolvers)
        headers = mock_get.call_args.kwargs["headers"]
        assert headers["User-Agent"] == "proxy-agent/1.0"
        assert headers["Cookie"] == "ezproxy=SESSION"

    @patch("biolit.fetchers.custom_resolvers.fetch_via_landing_page", return_value=FAKE_PDF)
    @patch("biolit.fetchers.custom_resolvers.requests.get")
    def test_scrape_flag_delegates_to_landing_page(self, mock_get, mock_scrape):
        mock_get.return_value = _mock_response(
            200, content=b"<html>openurl landing</html>", url="https://proxy.example.edu/article"
        )
        resolvers = [{"url_template": "https://proxy.example.edu/?u=https://doi.org/{doi}", "scrape": True}]
        assert fetch_via_custom_resolvers(doi="10.1/a", resolvers=resolvers) == FAKE_PDF
        mock_scrape.assert_called_once()

    @patch("biolit.fetchers.custom_resolvers.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")
        resolvers = [{"url_template": "https://a/{doi}"}]
        assert fetch_via_custom_resolvers(doi="10.1/a", resolvers=resolvers) is None

    @patch("biolit.fetchers.custom_resolvers.requests.get")
    def test_skips_entry_needing_unavailable_value(self, mock_get):
        # url_template needs {url}; only a doi is supplied → entry skipped, no request.
        resolvers = [{"url_template": "https://a/?u={url}"}]
        assert fetch_via_custom_resolvers(doi="10.1/a", resolvers=resolvers) is None
        mock_get.assert_not_called()

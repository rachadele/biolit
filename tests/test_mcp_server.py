"""Tests for the MCP server tool functions.

Covers: resolve_doi, screen_by_doi (MCP wrapper), lookup_s2_pdf.

These tools are thin wrappers around existing pipeline/fetcher functions, so tests
mock those dependencies directly rather than making real network calls.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

# mcp_server.py initialises _llm at import time, which requires ANTHROPIC_API_KEY.
# Set a dummy value before the module is imported so CI doesn't raise EnvironmentError.
# All tests mock the underlying functions so the fake key is never used.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-ci")


# ---------------------------------------------------------------------------
# Module import helpers
# ---------------------------------------------------------------------------

def _import_tools():
    """Return the three new tool functions from the MCP server module."""
    from biolit.mcp_server import resolve_doi, screen_by_doi, lookup_s2_pdf
    return resolve_doi, screen_by_doi, lookup_s2_pdf


# ---------------------------------------------------------------------------
# resolve_doi
# ---------------------------------------------------------------------------

class TestResolveDoi:
    @patch("biolit.mcp_server.doi_to_pmcid")
    @patch("biolit.mcp_server.doi_to_pmid")
    def test_returns_pmid_and_pmcid(self, mock_pmid, mock_pmcid):
        mock_pmid.return_value = "41757081"
        mock_pmcid.return_value = "PMC12934622"
        resolve_doi, _, _ = _import_tools()
        result = resolve_doi("10.64898/2026.02.16.706214")
        assert result == {
            "doi": "10.64898/2026.02.16.706214",
            "pmid": "41757081",
            "pmcid": "PMC12934622",
        }

    @patch("biolit.mcp_server.doi_to_pmcid")
    @patch("biolit.mcp_server.doi_to_pmid")
    def test_returns_null_when_doi_not_in_pubmed(self, mock_pmid, mock_pmcid):
        mock_pmid.return_value = None
        mock_pmcid.return_value = None
        resolve_doi, _, _ = _import_tools()
        result = resolve_doi("10.9999/nonexistent")
        assert result["pmid"] is None
        assert result["pmcid"] is None


# ---------------------------------------------------------------------------
# screen_by_doi (MCP wrapper)
# ---------------------------------------------------------------------------

class TestMcpScreenByDoi:
    def _fake_screen_result(self, relevant=True):
        return {
            "relevant": relevant,
            "reason": "Matches schizophrenia genomics criterion.",
            "text_source": "preprint_abstract",
            "doi": "10.64898/2026.02.16.706214",
        }

    @patch("biolit.mcp_server._screen_by_doi")
    def test_delegates_to_screen_by_doi(self, mock_screen):
        mock_screen.return_value = self._fake_screen_result()
        _, screen_by_doi, _ = _import_tools()
        doi = "10.64898/2026.02.16.706214"
        result = screen_by_doi(doi, "Is this about schizophrenia genomics?")
        mock_screen.assert_called_once()
        assert mock_screen.call_args[0][1] == doi

    @patch("biolit.mcp_server._screen_by_doi")
    def test_passes_unpaywall_email_arg(self, mock_screen):
        mock_screen.return_value = self._fake_screen_result()
        _, screen_by_doi, _ = _import_tools()
        screen_by_doi("10.64898/2026.02.16.706214", "criterion", unpaywall_email="user@example.com")
        _, kwargs = mock_screen.call_args
        assert kwargs.get("unpaywall_email") == "user@example.com"

    @patch.dict(os.environ, {"UNPAYWALL_EMAIL": "env@example.com"})
    @patch("biolit.mcp_server._screen_by_doi")
    def test_falls_back_to_env_var_email(self, mock_screen):
        mock_screen.return_value = self._fake_screen_result()
        _, screen_by_doi, _ = _import_tools()
        screen_by_doi("10.64898/2026.02.16.706214", "criterion")
        _, kwargs = mock_screen.call_args
        assert kwargs.get("unpaywall_email") == "env@example.com"


# ---------------------------------------------------------------------------
# lookup_s2_pdf
# ---------------------------------------------------------------------------

class TestLookupS2Pdf:
    @patch("biolit.mcp_server.get_s2_pdf_url")
    def test_returns_available_true_when_url_found(self, mock_url):
        mock_url.return_value = "https://arxiv.org/pdf/2021.12345.pdf"
        _, _, lookup_s2_pdf = _import_tools()
        result = lookup_s2_pdf("10.1101/2021.11.01.466731")
        assert result["available"] is True
        assert result["pdf_url"] == "https://arxiv.org/pdf/2021.12345.pdf"

    @patch("biolit.mcp_server.get_s2_pdf_url")
    def test_returns_available_false_when_no_url(self, mock_url):
        mock_url.return_value = None
        _, _, lookup_s2_pdf = _import_tools()
        result = lookup_s2_pdf("10.64898/2026.02.16.706214")
        assert result["available"] is False
        assert result["pdf_url"] is None

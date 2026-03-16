"""Tests for the new MCP server tool functions added in the expand-fulltext-extract branch.

Covers: resolve_doi, screen_by_doi (MCP wrapper), lookup_s2_pdf.

These tools are thin wrappers around existing pipeline/fetcher functions, so tests
mock those dependencies directly rather than making real network calls.
"""
import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module import helpers
# ---------------------------------------------------------------------------
# mcp_server.py initialises _llm at import time, which requires ANTHROPIC_API_KEY.
# We import lazily inside each test class so that the patch is in effect when the
# module attribute is first accessed at call-time (not at import-time).  If the env
# var is already set (normal developer environment) the import succeeds regardless.

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

    @patch("biolit.mcp_server.doi_to_pmcid")
    @patch("biolit.mcp_server.doi_to_pmid")
    def test_doi_is_echoed_back(self, mock_pmid, mock_pmcid):
        mock_pmid.return_value = "12345678"
        mock_pmcid.return_value = None
        resolve_doi, _, _ = _import_tools()
        doi = "10.1038/s41588-021-00974-7"
        result = resolve_doi(doi)
        assert result["doi"] == doi

    @patch("biolit.mcp_server.doi_to_pmcid")
    @patch("biolit.mcp_server.doi_to_pmid")
    def test_pmcid_only_when_pmid_missing(self, mock_pmid, mock_pmcid):
        mock_pmid.return_value = None
        mock_pmcid.return_value = "PMC9999999"
        resolve_doi, _, _ = _import_tools()
        result = resolve_doi("10.1038/test")
        assert result["pmid"] is None
        assert result["pmcid"] == "PMC9999999"


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
        call_kwargs = mock_screen.call_args
        assert call_kwargs[0][1] == doi
        assert call_kwargs[0][2] == "Is this about schizophrenia genomics?"

    @patch("biolit.mcp_server._screen_by_doi")
    def test_passes_through_result(self, mock_screen):
        expected = self._fake_screen_result(relevant=True)
        mock_screen.return_value = expected
        _, screen_by_doi, _ = _import_tools()
        result = screen_by_doi("10.64898/2026.02.16.706214", "criterion")
        assert result["relevant"] is True
        assert result["text_source"] == "preprint_abstract"

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

    @patch("biolit.mcp_server._screen_by_doi")
    def test_irrelevant_result_passed_through(self, mock_screen):
        mock_screen.return_value = self._fake_screen_result(relevant=False)
        _, screen_by_doi, _ = _import_tools()
        result = screen_by_doi("10.64898/2026.02.16.706214", "criterion")
        assert result["relevant"] is False


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

    @patch("biolit.mcp_server.get_s2_pdf_url")
    def test_doi_echoed_back(self, mock_url):
        mock_url.return_value = None
        _, _, lookup_s2_pdf = _import_tools()
        doi = "10.1038/s41588-021-00974-7"
        result = lookup_s2_pdf(doi)
        assert result["doi"] == doi

    @patch("biolit.mcp_server.get_s2_pdf_url")
    def test_calls_get_s2_pdf_url_with_doi(self, mock_url):
        mock_url.return_value = None
        _, _, lookup_s2_pdf = _import_tools()
        lookup_s2_pdf("10.1038/test")
        mock_url.assert_called_once_with("10.1038/test")

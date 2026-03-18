"""Tests for the MCP server tool functions.

Covers: resolve_doi, lookup_s2_pdf, run_pipeline.

These tools are thin wrappers around existing pipeline/fetcher functions, so tests
mock those dependencies directly rather than making real network calls.
"""
import os
from unittest.mock import patch

import pytest

# mcp_server.py initialises _llm at import time, which requires ANTHROPIC_API_KEY.
# Set a dummy value before the module is imported so CI doesn't raise EnvironmentError.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-ci")


# ---------------------------------------------------------------------------
# resolve_doi
# ---------------------------------------------------------------------------

class TestResolveDoi:
    @patch("biolit.mcp_server.doi_to_pmcid")
    @patch("biolit.mcp_server.doi_to_pmid")
    def test_returns_pmid_and_pmcid(self, mock_pmid, mock_pmcid):
        mock_pmid.return_value = "41757081"
        mock_pmcid.return_value = "PMC12934622"
        from biolit.mcp_server import resolve_doi
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
        from biolit.mcp_server import resolve_doi
        result = resolve_doi("10.9999/nonexistent")
        assert result["pmid"] is None
        assert result["pmcid"] is None


# ---------------------------------------------------------------------------
# lookup_s2_pdf
# ---------------------------------------------------------------------------

class TestLookupS2Pdf:
    @patch("biolit.mcp_server.get_s2_pdf_url")
    def test_returns_available_true_when_url_found(self, mock_url):
        mock_url.return_value = "https://arxiv.org/pdf/2021.12345.pdf"
        from biolit.mcp_server import lookup_s2_pdf
        result = lookup_s2_pdf("10.1101/2021.11.01.466731")
        assert result["available"] is True
        assert result["pdf_url"] == "https://arxiv.org/pdf/2021.12345.pdf"

    @patch("biolit.mcp_server.get_s2_pdf_url")
    def test_returns_available_false_when_no_url(self, mock_url):
        mock_url.return_value = None
        from biolit.mcp_server import lookup_s2_pdf
        result = lookup_s2_pdf("10.64898/2026.02.16.706214")
        assert result["available"] is False
        assert result["pdf_url"] is None


# ---------------------------------------------------------------------------
# run_pipeline
# ---------------------------------------------------------------------------

class TestRunPipeline:
    """run_pipeline is the unified MCP entry point for any mix of IDs."""

    def _call(self, ids, csv_path=None, relevant_count=1):
        """Invoke run_pipeline with _run mocked to return (csv_path, relevant_count)."""
        from biolit.mcp_server import run_pipeline
        with patch("biolit.mcp_server._run", return_value=(csv_path, relevant_count)) as mock_run:
            result = run_pipeline(ids=ids, criterion="Is this schizophrenia genomics?", fields="summary")
            return result, mock_run

    def test_pmid_passes_through(self):
        result, mock_run = self._call("41795042", csv_path="/tmp/run/results.csv")
        assert mock_run.call_args.kwargs["ids"] == ["41795042"]
        assert result["output_path"] == "/tmp/run/results.csv"
        assert result["relevant_count"] == 1

    def test_doi_passes_through(self):
        result, mock_run = self._call("10.1101/2025.03.17.25324098", csv_path="/tmp/run/results.csv")
        assert mock_run.call_args.kwargs["ids"] == ["10.1101/2025.03.17.25324098"]

    def test_geo_passes_through(self):
        result, mock_run = self._call("GSE53987", csv_path="/tmp/run/results.csv")
        assert mock_run.call_args.kwargs["ids"] == ["GSE53987"]

    def test_mixed_ids(self):
        result, mock_run = self._call("41795042,GSE53987,10.1101/2025.03.17.25324098",
                                      csv_path="/tmp/run/results.csv", relevant_count=2)
        assert mock_run.call_args.kwargs["ids"] == [
            "41795042", "GSE53987", "10.1101/2025.03.17.25324098"
        ]
        assert result["relevant_count"] == 2

    def test_no_relevant_records(self):
        result, _ = self._call("41795042", csv_path=None, relevant_count=0)
        assert result["output_path"] is None
        assert result["relevant_count"] == 0

    def test_passes_unpaywall_email(self):
        from biolit.mcp_server import run_pipeline
        with patch("biolit.mcp_server._run", return_value=(None, 0)) as mock_run:
            run_pipeline(ids="41795042", criterion="criterion", fields="summary",
                         unpaywall_email="user@example.com")
        assert mock_run.call_args.kwargs["unpaywall_email"] == "user@example.com"

    @patch.dict(os.environ, {"UNPAYWALL_EMAIL": "env@example.com"})
    def test_falls_back_to_env_var_email(self):
        from biolit.mcp_server import run_pipeline
        with patch("biolit.mcp_server._run", return_value=(None, 0)) as mock_run:
            run_pipeline(ids="41795042", criterion="criterion", fields="summary")
        assert mock_run.call_args.kwargs["unpaywall_email"] == "env@example.com"


# ---------------------------------------------------------------------------
# run_pipeline — config_path parameter and optional criterion/fields
# ---------------------------------------------------------------------------

class TestRunPipelineConfig:
    """Tests for run_pipeline config_path handling and optional criterion/fields."""

    def test_empty_criterion_passes_none_to_run(self):
        """Empty criterion string → criterion=None (no screening)."""
        from biolit.mcp_server import run_pipeline
        with patch("biolit.mcp_server._run", return_value=(None, 0)) as mock_run:
            run_pipeline(ids="41795042", criterion="", fields="summary")
        assert mock_run.call_args.kwargs["criterion"] is None

    def test_empty_fields_uses_default_fields(self):
        """Empty fields string → DEFAULT_FIELDS from cli module."""
        from biolit.mcp_server import run_pipeline
        from biolit.cli import DEFAULT_FIELDS
        with patch("biolit.mcp_server._run", return_value=(None, 0)) as mock_run:
            run_pipeline(ids="41795042", criterion="criterion", fields="")
        assert mock_run.call_args.kwargs["fields_description"] == DEFAULT_FIELDS

    def test_config_path_loads_criterion_and_fields(self, tmp_path):
        """config_path loads a config file and uses its criterion and fields."""
        import json
        cfg = {"criterion": "Config criterion", "fields": "methodology, summary"}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))

        from biolit.mcp_server import run_pipeline
        with patch("biolit.mcp_server._run", return_value=(None, 0)) as mock_run:
            result = run_pipeline(ids="41795042", config_path=str(config_file))

        assert "error" not in result
        assert mock_run.call_args.kwargs["criterion"] == "Config criterion"
        assert mock_run.call_args.kwargs["fields_description"] == "methodology, summary"

    def test_explicit_criterion_overrides_config(self, tmp_path):
        """An explicit criterion argument overrides the config file value."""
        import json
        cfg = {"criterion": "Config criterion"}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))

        from biolit.mcp_server import run_pipeline
        with patch("biolit.mcp_server._run", return_value=(None, 0)) as mock_run:
            run_pipeline(ids="41795042", criterion="Explicit criterion",
                         config_path=str(config_file))

        assert mock_run.call_args.kwargs["criterion"] == "Explicit criterion"

    def test_explicit_fields_override_config(self, tmp_path):
        """An explicit fields argument overrides the config file value."""
        import json
        cfg = {"fields": "config_field"}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))

        from biolit.mcp_server import run_pipeline
        with patch("biolit.mcp_server._run", return_value=(None, 0)) as mock_run:
            run_pipeline(ids="41795042", fields="explicit_field",
                         config_path=str(config_file))

        assert mock_run.call_args.kwargs["fields_description"] == "explicit_field"

    def test_bad_config_path_returns_error_dict(self, tmp_path):
        """A config_path that doesn't exist returns an error dict, not a raised exception."""
        missing = str(tmp_path / "nonexistent.json")
        from biolit.mcp_server import run_pipeline
        result = run_pipeline(ids="41795042", config_path=missing)
        assert "error" in result
        assert isinstance(result["error"], str)

    def test_bad_config_content_returns_error_dict(self, tmp_path):
        """A config_path with unknown keys returns an error dict."""
        import json
        config_file = tmp_path / "bad.json"
        config_file.write_text(json.dumps({"unknown_key": "value"}))

        from biolit.mcp_server import run_pipeline
        result = run_pipeline(ids="41795042", config_path=str(config_file))
        assert "error" in result
        assert isinstance(result["error"], str)

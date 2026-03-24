"""Tests for CLI input detection and dispatch logic."""
import json
import pathlib
import sys
import pytest
from unittest.mock import patch, MagicMock, call

from biolit.cli import _screen_main, _run_main, DEFAULT_CRITERION, DEFAULT_FIELDS
from biolit.utils import read_geo_file, read_pmids_file

REAL_PMIDS = ["41795042", "41792186", "41785323"]
REAL_ACCESSIONS = ["GSE53987", "GSE12345"]


class TestReadGeoFile:
    def test_reads_accessions_skipping_comments_and_blanks(self, tmp_path):
        f = tmp_path / "accessions.txt"
        f.write_text("# schizophrenia datasets\nGSE53987\n\nGSE12345\n")
        assert read_geo_file(str(f)) == ["GSE53987", "GSE12345"]

    def test_returns_empty_for_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        assert read_geo_file(str(f)) == []


class TestReadPmidsFile:
    def test_reads_pmids_skipping_comments_blanks_and_non_digits(self, tmp_path):
        f = tmp_path / "pmids.txt"
        f.write_text(f"# from alert\n{REAL_PMIDS[0]}\n\nnot_a_pmid\n{REAL_PMIDS[1]}\n")
        assert read_pmids_file(str(f)) == [REAL_PMIDS[0], REAL_PMIDS[1]]

    def test_returns_empty_for_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        assert read_pmids_file(str(f)) == []



class TestFulltextFlagRemoved:
    """--fulltext is no longer a valid flag in either subcommand."""

    def test_screen_rejects_fulltext_flag(self):
        with pytest.raises(SystemExit):
            _screen_main(["--pmid", "41795042", "--criterion", "x", "--fulltext"])

    def test_run_rejects_fulltext_flag(self, tmp_path):
        pmids_file = tmp_path / "p.txt"
        pmids_file.write_text("41795042\n")
        with pytest.raises(SystemExit):
            _run_main([str(pmids_file), "--criterion", "x", "--fields", "summary", "--fulltext"])


# ---------------------------------------------------------------------------
# _run_main — --config flag
# ---------------------------------------------------------------------------

class TestRunMainConfig:
    """Tests for --config flag and changed defaults in _run_main."""

    def _make_ids_file(self, tmp_path, content="41795042\n"):
        f = tmp_path / "ids.txt"
        f.write_text(content)
        return str(f)

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_config_loads_criterion_and_fields(self, mock_llm, mock_run, tmp_path):
        """--config loads a config file and passes its values to run()."""
        cfg = {"criterion": "Config criterion", "fields": "methodology, summary"}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))
        ids_file = self._make_ids_file(tmp_path)

        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()

        with patch("biolit.cli.load_config", return_value=cfg) as mock_load:
            _run_main([ids_file, "--config", str(config_file)])
            mock_load.assert_called_once_with(str(config_file))

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["criterion"] == "Config criterion"
        assert call_kwargs["fields_description"] == "methodology, summary"

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_cli_flag_overrides_config_criterion(self, mock_llm, mock_run, tmp_path):
        """--criterion CLI flag overrides the criterion in the config file."""
        cfg = {"criterion": "Config criterion"}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))
        ids_file = self._make_ids_file(tmp_path)

        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()

        with patch("biolit.cli.load_config", return_value=cfg):
            _run_main([ids_file, "--config", str(config_file),
                       "--criterion", "CLI criterion"])

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["criterion"] == "CLI criterion"

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_default_flag_overrides_config(self, mock_llm, mock_run, tmp_path):
        """--default uses DEFAULT_CRITERION and DEFAULT_FIELDS, ignoring config values."""
        cfg = {"criterion": "Config criterion", "fields": "config_field"}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))
        ids_file = self._make_ids_file(tmp_path)

        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()

        with patch("biolit.cli.load_config", return_value=cfg):
            _run_main([ids_file, "--config", str(config_file), "--default"])

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["criterion"] == DEFAULT_CRITERION
        assert call_kwargs["fields_description"] == DEFAULT_FIELDS

    @patch("biolit.cli.get_llm_client")
    def test_missing_config_file_exits_with_error(self, mock_llm, tmp_path, capsys):
        """A --config path that doesn't exist causes sys.exit(1) with an error message."""
        ids_file = self._make_ids_file(tmp_path)
        missing_config = str(tmp_path / "nonexistent.json")

        mock_llm.return_value = MagicMock()

        with pytest.raises(SystemExit) as exc_info:
            _run_main([ids_file, "--config", missing_config])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error" in captured.out or "error" in captured.out.lower()

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_no_criterion_defaults_to_none(self, mock_llm, mock_run, tmp_path):
        """Without --criterion, --default, or config criterion, criterion is None (no screening)."""
        ids_file = self._make_ids_file(tmp_path)
        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()

        _run_main([ids_file])

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["criterion"] is None

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_no_fields_defaults_to_default_fields(self, mock_llm, mock_run, tmp_path):
        """Without --fields or config, fields_description defaults to DEFAULT_FIELDS."""
        ids_file = self._make_ids_file(tmp_path)
        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()

        _run_main([ids_file])

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["fields_description"] == DEFAULT_FIELDS

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_no_interactive_prompt_without_criterion(self, mock_llm, mock_run, tmp_path):
        """No interactive input() prompt is triggered when --criterion is omitted."""
        ids_file = self._make_ids_file(tmp_path)
        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()

        # If input() were called, it would raise EOFError in a non-interactive context.
        # Patching it to raise ensures the test fails if input() is invoked.
        with patch("builtins.input", side_effect=AssertionError("input() should not be called")):
            _run_main([ids_file])  # should not raise

    @patch("biolit.cli.get_llm_client")
    def test_bad_config_json_exits_with_error(self, mock_llm, tmp_path, capsys):
        """A config file with invalid JSON causes sys.exit(1)."""
        config_file = tmp_path / "bad.json"
        config_file.write_text("{not valid json}")
        ids_file = self._make_ids_file(tmp_path)

        mock_llm.return_value = MagicMock()

        with pytest.raises(SystemExit) as exc_info:
            _run_main([ids_file, "--config", str(config_file)])

        assert exc_info.value.code == 1


class TestRunMainInputFileInConfig:
    """Tests for input_file key in the JSON config."""

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_config_input_file_eml_reads_pmids(self, mock_llm, mock_run, tmp_path):
        """input_file in config pointing to an .eml is parsed for PMIDs."""
        eml = tmp_path / "alert.eml"
        eml.write_text("dummy eml content")
        cfg = {"input_file": str(eml)}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))

        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()

        with patch("biolit.cli.load_config", return_value=cfg), \
             patch("biolit.cli.read_eml_body", return_value="body text") as mock_eml, \
             patch("biolit.cli.extract_pmids", return_value=["11111111", "22222222"]) as mock_extract:
            _run_main(["--config", str(config_file)])

        mock_eml.assert_called_once_with(str(eml))
        mock_extract.assert_called_once_with("body text")
        ids_passed = mock_run.call_args.kwargs["ids"]
        assert ids_passed == ["11111111", "22222222"]

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_config_input_file_txt_reads_ids(self, mock_llm, mock_run, tmp_path):
        """input_file in config pointing to a .txt file is read as an ID list."""
        txt = tmp_path / "ids.txt"
        txt.write_text("41795042\nGSE53987\n")
        cfg = {"input_file": str(txt)}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))

        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()

        with patch("biolit.cli.load_config", return_value=cfg):
            _run_main(["--config", str(config_file)])

        ids_passed = mock_run.call_args.kwargs["ids"]
        assert ids_passed == ["41795042", "GSE53987"]

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_cli_positional_overrides_config_input_file(self, mock_llm, mock_run, tmp_path):
        """A positional input_file arg takes precedence over input_file in the config."""
        cli_txt = tmp_path / "cli_ids.txt"
        cli_txt.write_text("99999999\n")
        config_txt = tmp_path / "config_ids.txt"
        config_txt.write_text("11111111\n")
        cfg = {"input_file": str(config_txt)}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))

        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()

        with patch("biolit.cli.load_config", return_value=cfg):
            _run_main([str(cli_txt), "--config", str(config_file)])

        ids_passed = mock_run.call_args.kwargs["ids"]
        assert ids_passed == ["99999999"]

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_config_ids_takes_precedence_over_config_input_file(self, mock_llm, mock_run, tmp_path):
        """ids in config takes precedence over input_file in config."""
        txt = tmp_path / "ids.txt"
        txt.write_text("should_not_be_read\n")
        cfg = {"ids": "41795042,41792186", "input_file": str(txt)}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))

        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()

        with patch("biolit.cli.load_config", return_value=cfg):
            _run_main(["--config", str(config_file)])

        ids_passed = mock_run.call_args.kwargs["ids"]
        assert ids_passed == ["41795042", "41792186"]

    @patch("biolit.cli.get_llm_client")
    def test_no_input_at_all_exits_with_error(self, mock_llm, tmp_path, capsys):
        """No positional arg, no --ids, no ids or input_file in config → sys.exit(1)."""
        cfg = {"criterion": "Is this relevant?"}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))

        mock_llm.return_value = MagicMock()

        with patch("biolit.cli.load_config", return_value=cfg), \
             pytest.raises(SystemExit) as exc_info:
            _run_main(["--config", str(config_file)])

        assert exc_info.value.code == 1
        assert "Error" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# --markdown / --md flag
# ---------------------------------------------------------------------------

class TestMarkdownFlag:
    """Tests for the --markdown (--md) CLI flag."""

    def _make_ids_file(self, tmp_path, content="41795042\n"):
        f = tmp_path / "ids.txt"
        f.write_text(content)
        return str(f)

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_markdown_flag_passes_markdown_true(self, mock_llm, mock_run, tmp_path):
        """--markdown sets markdown=True when calling run()."""
        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()
        ids_file = self._make_ids_file(tmp_path)

        _run_main([ids_file, "--markdown"])

        _, kwargs = mock_run.call_args
        assert kwargs.get("markdown") is True

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_md_alias_passes_markdown_true(self, mock_llm, mock_run, tmp_path):
        """--md is an alias for --markdown and also sets markdown=True."""
        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()
        ids_file = self._make_ids_file(tmp_path)

        _run_main([ids_file, "--md"])

        _, kwargs = mock_run.call_args
        assert kwargs.get("markdown") is True

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_no_markdown_flag_passes_markdown_false(self, mock_llm, mock_run, tmp_path):
        """Without --markdown, markdown=False is passed to run()."""
        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()
        ids_file = self._make_ids_file(tmp_path)

        _run_main([ids_file])

        _, kwargs = mock_run.call_args
        assert kwargs.get("markdown") is False

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_markdown_from_config_file(self, mock_llm, mock_run, tmp_path):
        """markdown=true in a config file is forwarded to run()."""
        cfg = {"markdown": True}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))
        ids_file = self._make_ids_file(tmp_path)

        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()

        with patch("biolit.cli.load_config", return_value=cfg):
            _run_main([ids_file, "--config", str(config_file)])

        _, kwargs = mock_run.call_args
        assert kwargs.get("markdown") is True

    @patch("biolit.cli.run")
    @patch("biolit.cli.get_llm_client")
    def test_cli_flag_overrides_config_markdown_false(self, mock_llm, mock_run, tmp_path):
        """--markdown CLI flag wins even when config has markdown=false (or absent)."""
        cfg = {}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(cfg))
        ids_file = self._make_ids_file(tmp_path)

        mock_run.return_value = (None, 0)
        mock_llm.return_value = MagicMock()

        with patch("biolit.cli.load_config", return_value=cfg):
            _run_main([ids_file, "--config", str(config_file), "--markdown"])

        _, kwargs = mock_run.call_args
        assert kwargs.get("markdown") is True

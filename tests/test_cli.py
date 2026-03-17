"""Tests for CLI input detection and dispatch logic."""
import pathlib
import pytest
from unittest.mock import patch, MagicMock

from biolit.cli import _screen_main, _run_main
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

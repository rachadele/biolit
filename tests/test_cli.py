"""Tests for CLI input detection and dispatch logic."""
import pathlib
from unittest.mock import patch, MagicMock
import pytest

from biolit.cli import _peek_first_value, _resolve_dois
from biolit.utils import read_geo_file, read_pmids_file

REAL_PMIDS = ["41795042", "41792186", "41785323"]
REAL_ACCESSIONS = ["GSE53987", "GSE12345"]


class TestPeekFirstValue:
    def test_returns_first_non_blank_non_comment_line(self, tmp_path):
        f = tmp_path / "input.txt"
        f.write_text("# comment\n\nGSE53987\nGSE12345\n")
        assert _peek_first_value(str(f)) == "GSE53987"

    def test_returns_none_for_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        assert _peek_first_value(str(f)) is None


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


class TestResolveDois:
    @patch("biolit.cli.doi_to_pmid")
    def test_resolves_dois_to_pmids(self, mock_resolve):
        mock_resolve.side_effect = ["11111111", "22222222"]
        result = _resolve_dois(["10.1038/a", "10.1038/b"])
        assert result == ["11111111", "22222222"]

    @patch("biolit.cli.doi_to_pmid")
    def test_skips_unresolvable_dois(self, mock_resolve):
        mock_resolve.side_effect = ["11111111", None]
        result = _resolve_dois(["10.1038/a", "10.9999/bad"])
        assert result == ["11111111"]

    @patch("biolit.cli.doi_to_pmid")
    def test_returns_empty_list_when_none_resolve(self, mock_resolve):
        mock_resolve.return_value = None
        result = _resolve_dois(["10.9999/bad"])
        assert result == []

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

    def test_returns_none_for_comments_only(self, tmp_path):
        f = tmp_path / "comments.txt"
        f.write_text("# just a comment\n# another\n")
        assert _peek_first_value(str(f)) is None

    def test_returns_first_pmid(self, tmp_path):
        f = tmp_path / "pmids.txt"
        f.write_text("\n".join(REAL_PMIDS))
        assert _peek_first_value(str(f)) == REAL_PMIDS[0]


class TestReadGeoFile:
    def test_reads_accessions(self, tmp_path):
        f = tmp_path / "accessions.txt"
        f.write_text("\n".join(REAL_ACCESSIONS) + "\n")
        assert read_geo_file(str(f)) == REAL_ACCESSIONS

    def test_ignores_comments(self, tmp_path):
        f = tmp_path / "accessions.txt"
        f.write_text("# schizophrenia datasets\nGSE53987\n# another comment\nGSE12345\n")
        assert read_geo_file(str(f)) == ["GSE53987", "GSE12345"]

    def test_ignores_blank_lines(self, tmp_path):
        f = tmp_path / "accessions.txt"
        f.write_text("GSE53987\n\n\nGSE12345\n")
        assert read_geo_file(str(f)) == ["GSE53987", "GSE12345"]

    def test_returns_empty_for_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        assert read_geo_file(str(f)) == []


class TestReadPmidsFile:
    def test_reads_pmids(self, tmp_path):
        f = tmp_path / "pmids.txt"
        f.write_text("\n".join(REAL_PMIDS) + "\n")
        assert read_pmids_file(str(f)) == REAL_PMIDS

    def test_ignores_comments(self, tmp_path):
        f = tmp_path / "pmids.txt"
        f.write_text(f"# from alert.eml\n{REAL_PMIDS[0]}\n# another\n{REAL_PMIDS[1]}\n")
        assert read_pmids_file(str(f)) == [REAL_PMIDS[0], REAL_PMIDS[1]]

    def test_ignores_blank_lines(self, tmp_path):
        f = tmp_path / "pmids.txt"
        f.write_text(f"{REAL_PMIDS[0]}\n\n{REAL_PMIDS[1]}\n")
        assert read_pmids_file(str(f)) == [REAL_PMIDS[0], REAL_PMIDS[1]]

    def test_ignores_non_digit_lines(self, tmp_path):
        f = tmp_path / "pmids.txt"
        f.write_text(f"{REAL_PMIDS[0]}\nnot_a_pmid\n{REAL_PMIDS[1]}\n")
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


class TestDoiFileDetection:
    def test_doi_file_detected_by_10_prefix(self, tmp_path):
        f = tmp_path / "dois.txt"
        f.write_text("10.1038/s41588-021-00974-7\n10.1016/j.biopsych.2021.01.005\n")
        first = _peek_first_value(str(f))
        assert first.startswith("10.")

    def test_pmid_file_not_detected_as_doi(self, tmp_path):
        f = tmp_path / "pmids.txt"
        f.write_text("41795042\n41792186\n")
        first = _peek_first_value(str(f))
        assert not first.startswith("10.")


class TestInputTypeDetection:
    """Test that the CLI correctly identifies input type from file contents."""

    def test_geo_file_detected_by_gse_prefix(self, tmp_path):
        f = tmp_path / "input.txt"
        f.write_text("GSE53987\nGSE12345\n")
        first = _peek_first_value(str(f))
        assert first.upper().startswith(("GSE", "GDS", "GSM", "GPL"))

    def test_pmid_file_not_detected_as_geo(self, tmp_path):
        f = tmp_path / "pmids.txt"
        f.write_text("\n".join(REAL_PMIDS))
        first = _peek_first_value(str(f))
        assert not first.upper().startswith(("GSE", "GDS", "GSM", "GPL"))

    def test_gds_prefix_detected_as_geo(self, tmp_path):
        f = tmp_path / "input.txt"
        f.write_text("GDS1234\n")
        first = _peek_first_value(str(f))
        assert first.upper().startswith(("GSE", "GDS", "GSM", "GPL"))

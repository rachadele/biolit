"""Tests using the real PubMed alert email (test1.eml).

These tests verify that the email parsing works correctly on the actual
HTML-format alert email from NCBI. They do NOT call the network or any LLM.
"""
import pathlib

from pubmed_screener.utils import read_eml_body, extract_pmids
from tests.conftest import TEST1_PMIDS

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


class TestTest1EmlParsing:
    """Verify PMID extraction from the real HTML-format alert email."""

    def test_body_is_non_empty(self, test1_eml_path):
        body = read_eml_body(str(test1_eml_path))
        assert isinstance(body, str)
        assert len(body) > 100

    def test_body_contains_pubmed_html(self, test1_eml_path):
        body = read_eml_body(str(test1_eml_path))
        assert "PubMed" in body

    def test_extracts_correct_number_of_pmids(self, test1_eml_path):
        body = read_eml_body(str(test1_eml_path))
        pmids = extract_pmids(body)
        assert len(pmids) == 18, (
            f"Expected 18 PMIDs, got {len(pmids)}: {pmids}"
        )

    def test_extracts_all_known_pmids(self, test1_eml_path):
        body = read_eml_body(str(test1_eml_path))
        pmids = extract_pmids(body)
        for expected in TEST1_PMIDS:
            assert expected in pmids, f"PMID {expected} not found in extracted list"

    def test_pmids_are_unique(self, test1_eml_path):
        body = read_eml_body(str(test1_eml_path))
        pmids = extract_pmids(body)
        assert len(pmids) == len(set(pmids)), "Duplicate PMIDs found"

    def test_first_pmid_is_most_recent(self, test1_eml_path):
        """PubMed alerts list newest papers first."""
        body = read_eml_body(str(test1_eml_path))
        pmids = extract_pmids(body)
        assert pmids[0] == TEST1_PMIDS[0]

    def test_plain_text_pattern_still_works(self):
        """Backward-compat: old plain-text 'PMID: XXXXXXXX' format still parsed."""
        body = "PMID: 12345678\nSome text\nPMID: 87654321"
        pmids = extract_pmids(body)
        assert pmids == ["12345678", "87654321"]



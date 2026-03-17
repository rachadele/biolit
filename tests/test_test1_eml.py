"""Tests using the real PubMed alert email (test1.eml).

These tests verify that the email parsing works correctly on the actual
HTML-format alert email from NCBI. They do NOT call the network or any LLM.
"""
import pathlib

from biolit.utils import read_eml_body, extract_pmids
from tests.conftest import TEST1_PMIDS

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


class TestTest1EmlParsing:
    """Verify PMID extraction from the real HTML-format alert email."""

    def test_extracts_correct_pmids(self, test1_eml_path):
        body = read_eml_body(str(test1_eml_path))
        pmids = extract_pmids(body)
        assert len(pmids) == 18
        assert set(pmids) == set(TEST1_PMIDS)

    def test_plain_text_format_still_parsed(self):
        """Backward-compat: old plain-text 'PMID: XXXXXXXX' format still parsed."""
        body = "PMID: 12345678\nSome text\nPMID: 87654321"
        assert extract_pmids(body) == ["12345678", "87654321"]

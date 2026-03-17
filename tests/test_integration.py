"""Real network + LLM integration tests.

These tests make live HTTP and LLM calls — they are skipped automatically when
ANTHROPIC_API_KEY is not set (e.g. in CI without credentials).

Run explicitly with:
    pytest tests/test_integration.py -v
"""
import os
import pytest

from biolit.llm import get_llm_client
from biolit.pipeline import screen_by_doi

MEDRXIV_DOI = "10.1101/2025.03.17.25324098"

DEFAULT_CRITERION = (
    "Is this paper SPECIFICALLY about schizophrenia AND does it use genetics "
    "or genomics methods (e.g. GWAS, WGS, scRNA-seq, proteomics, gene expression)?"
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)


class TestScreenByDoiReal:
    def test_medrxiv_doi_screened_relevant(self):
        """medRxiv preprint 10.1101/2025.03.17.25324098 should be relevant
        under the default schizophrenia genomics criterion."""
        client = get_llm_client("anthropic")
        result = screen_by_doi(client, MEDRXIV_DOI, DEFAULT_CRITERION)

        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        assert result["relevant"] is True, (
            f"Expected relevant=True but got: {result.get('reason')}"
        )
        assert result["text_source"] in {
            "preprint_fulltext", "europepmc_fulltext", "unpaywall_pdf",
            "s2_pdf", "preprint_abstract",
        }
        assert result.get("doi") == MEDRXIV_DOI

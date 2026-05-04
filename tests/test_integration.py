"""Real network + LLM integration tests.

These tests make live HTTP and LLM calls. The provider is selected via
LLM_PROVIDER (default: anthropic); the test is skipped when the chosen
provider's API key is not set (e.g. in CI without credentials).

Run explicitly with:
    pytest tests/test_integration.py -v
    LLM_PROVIDER=openai pytest tests/test_integration.py -v
"""
import os
import pytest

from biolit.llm import get_llm_client
from biolit.llm.base import resolve_api_key
from biolit.pipeline import screen_by_doi

MEDRXIV_DOI = "10.1101/2025.03.17.25324098"

DEFAULT_CRITERION = (
    "Is this paper SPECIFICALLY about schizophrenia AND does it use genetics "
    "or genomics methods (e.g. GWAS, WGS, scRNA-seq, proteomics, gene expression)?"
)

_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic")
_PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}.get(_PROVIDER)

pytestmark = pytest.mark.skipif(
    _PROVIDER_KEY_ENV is None or not resolve_api_key(_PROVIDER_KEY_ENV),
    reason=f"{_PROVIDER_KEY_ENV or _PROVIDER} not set (env or keychain)",
)


class TestScreenByDoiReal:
    def test_medrxiv_doi_screened_relevant(self):
        """medRxiv preprint 10.1101/2025.03.17.25324098 should be relevant
        under the default schizophrenia genomics criterion."""
        client = get_llm_client(_PROVIDER, os.environ.get("LLM_MODEL"))
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

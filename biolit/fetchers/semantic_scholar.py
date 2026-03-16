"""Fetch open-access PDFs via the Semantic Scholar API.

Semantic Scholar indexes open-access PDFs for a large fraction of academic
papers, including bioRxiv/medRxiv preprints that are blocked by Cloudflare
when fetched directly.

API docs: https://api.semanticscholar.org/api-docs/
Authentication: set SEMANTIC_SCHOLAR_API_KEY environment variable.
Rate limit: 1 req/s with key (unauthenticated is much lower).
"""
import os
import requests

_S2_BASE = "https://api.semanticscholar.org/graph/v1"


def _s2_headers() -> dict:
    """Return request headers, injecting API key when available."""
    headers = {}
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def fetch_s2_pdf(doi: str) -> bytes | None:
    """Return PDF bytes for *doi* via Semantic Scholar's open-access PDF index.

    Returns None if no open-access PDF is found, the DOI is unknown to S2,
    or any network/parsing error occurs.
    """
    if not doi:
        return None

    pdf_url = get_s2_pdf_url(doi)
    if not pdf_url:
        return None

    try:
        resp = requests.get(pdf_url, headers=_s2_headers(), timeout=60)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "").lower()
        if "pdf" not in content_type and not pdf_url.endswith(".pdf"):
            return None
        return resp.content
    except Exception:
        return None


def get_s2_pdf_url(doi: str) -> str | None:
    """Return the open-access PDF URL for *doi* from Semantic Scholar, or None."""
    if not doi:
        return None
    try:
        resp = requests.get(
            f"{_S2_BASE}/paper/DOI:{doi}",
            params={"fields": "openAccessPdf"},
            headers=_s2_headers(),
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        oa = data.get("openAccessPdf") or {}
        return oa.get("url") or None
    except Exception:
        return None

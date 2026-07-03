"""Fetch open-access full-text PDFs via the CORE API.

CORE (https://core.ac.uk) aggregates open-access copies harvested from
institutional and subject repositories worldwide — a good source of green-OA
author manuscripts that neither Unpaywall nor OpenAlex surface.

The CORE API requires a free API key. This fetcher is therefore OPT-IN: with
no ``CORE_API_KEY`` in the environment it is a no-op returning None, so it can
sit in the fallback chain unconditionally without erroring.

API docs: https://api.core.ac.uk/docs/v3
"""
import os
import requests


_CORE_SEARCH = "https://api.core.ac.uk/v3/search/works"


def _core_api_key() -> str | None:
    return os.environ.get("CORE_API_KEY") or None


def fetch_via_core(doi: str, api_key: str | None = None) -> bytes | None:
    """Download an open-access PDF for *doi* using CORE.

    Requires a CORE API key (argument *api_key* or the ``CORE_API_KEY``
    environment variable). When no key is configured this is a no-op that
    returns None — so the fetcher can sit in the chain unconditionally.

    Returns raw PDF bytes (verified to start with ``%PDF``), or None if no OA
    copy is found, no key is configured, or any network/parsing error occurs.
    """
    if not doi:
        return None
    api_key = api_key or _core_api_key()
    if not api_key:
        return None

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = requests.post(
            _CORE_SEARCH,
            headers=headers,
            json={"q": f'doi:"{doi}"', "limit": 1},
            timeout=20,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if not results:
            return None
        download_url = results[0].get("downloadUrl")
        if not download_url:
            return None

        pdf_resp = requests.get(download_url, headers=headers, timeout=60)
        pdf_resp.raise_for_status()
        content = pdf_resp.content
        if content[:4] != b"%PDF":
            return None
        return content
    except Exception:
        return None

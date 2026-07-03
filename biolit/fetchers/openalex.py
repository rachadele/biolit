"""Fetch open-access full-text PDFs via the OpenAlex API.

OpenAlex (https://openalex.org) is a free, key-less catalogue of scholarly
works. Its per-work record exposes OA locations — including green-OA author
manuscripts deposited in institutional / preprint repositories that Unpaywall
sometimes misses. Those manuscripts frequently carry the full Methods section,
so OpenAlex is a useful extra hop in the full-text fallback chain.

No API key is required, but the polite pool wants a contact ``mailto`` and a
descriptive ``User-Agent``. We pass the caller's email (the same one used for
Unpaywall) when available.

API docs: https://docs.openalex.org/
"""
import os
import requests


_OPENALEX_WORK = "https://api.openalex.org/works/doi:{doi}"
_USER_AGENT = "biolit/1.0 (https://github.com/; OA full-text fetcher)"


def _candidate_pdf_urls(work: dict) -> list[str]:
    """Collect candidate PDF URLs from an OpenAlex work record, best first.

    Order: ``best_oa_location`` → ``primary_location`` → every entry in
    ``locations``. Duplicates are removed while preserving order.
    """
    urls: list[str] = []

    def _add(loc: dict | None) -> None:
        if isinstance(loc, dict):
            url = loc.get("pdf_url")
            if url:
                urls.append(url)

    _add(work.get("best_oa_location"))
    _add(work.get("primary_location"))
    for loc in work.get("locations") or []:
        _add(loc)

    seen: set[str] = set()
    deduped: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def fetch_via_openalex(doi: str, mailto: str | None = None) -> bytes | None:
    """Download an open-access PDF for *doi* using OpenAlex.

    *mailto* is an optional contact email used for OpenAlex's polite pool
    (falls back to the ``UNPAYWALL_EMAIL`` environment variable, since both
    are the caller's contact address). It is not required.

    Returns raw PDF bytes (verified to start with ``%PDF``), or None if no OA
    PDF is found or any network/parsing error occurs.
    """
    if not doi:
        return None
    mailto = mailto or os.environ.get("UNPAYWALL_EMAIL")
    headers = {"User-Agent": _USER_AGENT}
    params = {"mailto": mailto} if mailto else {}
    try:
        meta_resp = requests.get(
            _OPENALEX_WORK.format(doi=doi),
            params=params,
            headers=headers,
            timeout=15,
        )
        if meta_resp.status_code == 404:
            return None
        meta_resp.raise_for_status()
        work = meta_resp.json()

        for pdf_url in _candidate_pdf_urls(work):
            try:
                pdf_resp = requests.get(pdf_url, headers=headers, timeout=60)
                pdf_resp.raise_for_status()
            except Exception:
                continue
            content = pdf_resp.content
            # Verify it's a real PDF — many OA URLs redirect to HTML landings.
            if content[:4] == b"%PDF":
                return content
        return None
    except Exception:
        return None

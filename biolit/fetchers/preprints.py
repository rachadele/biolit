"""Fetch full text from bioRxiv / medRxiv preprint servers."""
import requests

_API_BASE = "https://api.biorxiv.org/details/{server}/{doi}/na/json"


def _is_preprint_doi(doi: str) -> bool:
    """Return True if the DOI belongs to a bioRxiv or medRxiv preprint."""
    if not doi:
        return False
    doi_lower = doi.lower()
    # 10.1101 is the legacy bioRxiv/medRxiv prefix
    # 10.64898 is the newer bioRxiv prefix (introduced 2025)
    return "10.1101/" in doi_lower or "10.64898/" in doi_lower


def fetch_preprint(doi: str) -> bytes | None:
    """Try to retrieve JATS XML for a bioRxiv or medRxiv preprint by DOI.

    Returns raw XML bytes, or None if unavailable.
    The JATS XML URL is taken directly from the API response, which handles
    the date-based path that newer DOI prefixes (10.64898) require.
    Note: biorxiv's JATS endpoint may return 403 (Cloudflare); callers should
    fall back to fetch_preprint_metadata() for abstract-only text in that case.
    """
    if not _is_preprint_doi(doi):
        return None

    for srv in ("biorxiv", "medrxiv"):
        try:
            api_url = _API_BASE.format(server=srv, doi=doi)
            resp = requests.get(api_url, timeout=15)
            resp.raise_for_status()
            collection = resp.json().get("collection", [])
            if not collection:
                continue
            jats_url = collection[0].get("jatsxml")
            if not jats_url:
                continue
            xml_resp = requests.get(jats_url, timeout=30)
            if xml_resp.status_code == 403:
                return None
            xml_resp.raise_for_status()
            return xml_resp.content
        except Exception:
            continue
    return None


def fetch_preprint_metadata(doi: str) -> dict | None:
    """Return title and abstract for a preprint from the biorxiv/medrxiv API.

    Used as a fallback when full-text JATS XML is blocked (e.g. Cloudflare 403).
    Returns a dict with keys: title, abstract, doi, server — or None if not found.
    """
    if not _is_preprint_doi(doi):
        return None

    for srv in ("biorxiv", "medrxiv"):
        try:
            api_url = _API_BASE.format(server=srv, doi=doi)
            resp = requests.get(api_url, timeout=15)
            resp.raise_for_status()
            collection = resp.json().get("collection", [])
            if not collection:
                continue
            record = collection[0]
            return {
                "title": record.get("title", doi),
                "abstract": record.get("abstract", ""),
                "doi": doi,
                "server": srv,
                "authors": record.get("authors") or None,
            }
        except Exception:
            continue
    return None


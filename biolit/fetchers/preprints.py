"""Fetch full text from bioRxiv / medRxiv preprint servers."""
import requests

_API_BASE = "https://api.biorxiv.org/details/{server}/{doi}/na/json"
_JATS_TEMPLATE = "https://www.biorxiv.org/content/{doi}.source.xml"
_MEDRXIV_JATS_TEMPLATE = "https://www.medrxiv.org/content/{doi}.source.xml"


def _is_preprint_doi(doi: str) -> str | None:
    """Return 'biorxiv' or 'medrxiv' if the DOI belongs to a preprint server."""
    if not doi:
        return None
    doi_lower = doi.lower()
    if "10.1101/" in doi_lower:
        # Both bioRxiv and medRxiv share the 10.1101 prefix; try biorxiv first
        return "biorxiv"
    return None


def fetch_preprint(doi: str) -> bytes | None:
    """Try to retrieve JATS XML for a bioRxiv or medRxiv preprint by DOI.

    Returns raw XML bytes, or None if the paper is not a preprint / fetch fails.
    """
    if not doi:
        return None
    server = _is_preprint_doi(doi)
    if not server:
        return None

    # Query the biorxiv API; fall back to medrxiv if no results
    for srv in ("biorxiv", "medrxiv"):
        try:
            api_url = _API_BASE.format(server=srv, doi=doi)
            resp = requests.get(api_url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            collection = data.get("collection", [])
            if not collection:
                continue
            # The API confirms this paper exists on this server — grab JATS XML
            jats_template = _JATS_TEMPLATE if srv == "biorxiv" else _MEDRXIV_JATS_TEMPLATE
            xml_url = jats_template.format(doi=doi)
            xml_resp = requests.get(xml_url, timeout=30)
            xml_resp.raise_for_status()
            return xml_resp.content
        except Exception:
            continue
    return None


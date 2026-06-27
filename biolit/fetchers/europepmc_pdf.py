"""Europe PMC open-access full-text PDF fetcher.

Complements :mod:`biolit.fetchers.europepmc` (which returns JATS XML). For
articles in the Europe PMC open-access subset, Europe PMC also serves a
rendered full-text PDF:

    GET https://www.ebi.ac.uk/europepmc/webservices/rest/{PMCID}/fullTextPDF

This catches papers whose OA PDF is reachable through Europe PMC even when the
JATS XML route returned nothing parseable. OA subset only — never a paywall
bypass.
"""
import requests

EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def _normalize_pmcid(pmcid: str) -> str:
    """Return a bare ``PMC#######`` identifier (Europe PMC wants the prefix)."""
    pmcid = pmcid.strip().upper()
    if not pmcid.startswith("PMC"):
        pmcid = f"PMC{pmcid}"
    return pmcid


def fetch_europepmc_pdf(
    pmid: str | None = None,
    doi: str | None = None,
    pmcid: str | None = None,
) -> bytes | None:
    """Return open-access full-text PDF bytes from Europe PMC, or None.

    At least one of *pmcid*, *pmid*, or *doi* must be provided. When only a
    PMID or DOI is given, it is resolved to a PMCID via the NCBI ID Converter
    (the same path :mod:`biolit.fetchers.europepmc` uses).

    Returns raw PDF bytes (verified to start with ``%PDF``), or None if the
    article is not in the OA subset or any network error occurs.
    """
    if not pmcid:
        from biolit.fetchers.pubmed import doi_to_pmcid, pmid_to_pmcid
        if pmid:
            pmcid = pmid_to_pmcid(pmid)
        if not pmcid and doi:
            pmcid = doi_to_pmcid(doi)
    if not pmcid:
        return None

    url = f"{EUROPE_PMC_BASE}/{_normalize_pmcid(pmcid)}/fullTextPDF"
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        content = resp.content
        if content[:4] != b"%PDF":
            return None
        return content
    except Exception:
        return None

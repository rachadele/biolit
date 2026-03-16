"""Europe PMC full-text fetcher.

Europe PMC exposes JATS XML for open-access articles via a simple REST endpoint:

    GET https://www.ebi.ac.uk/europepmc/webservices/rest/{source}/{id}/fullTextXML

where *source* is either ``PMC`` (using a PMCID) or ``MED`` (using a PMID).
Returns JATS XML on success, 404 when the article isn't available open-access.
"""
import requests

EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def fetch_europepmc_fulltext(
    pmid: str | None = None,
    doi: str | None = None,
) -> bytes | None:
    """Return JATS XML bytes from Europe PMC, or None if unavailable.

    At least one of *pmid* or *doi* must be provided.

    Priority:
      1. MED/{pmid}  — if pmid is known
      2. DOI → PMID/PMCID via NCBI ID Converter, then retry

    The PMC/{pmcid} endpoint is intentionally skipped — it overlaps heavily
    with NCBI PMC efetch (step 1 in the pipeline) and rarely adds coverage.
    """
    if pmid:
        xml = _get_fulltext_xml("MED", pmid)
        if xml:
            return xml

    # DOI fallback: resolve to PMID or PMCID and retry
    if doi and not pmid:
        from biolit.fetchers.pubmed import doi_to_pmid, doi_to_pmcid
        resolved_pmid = doi_to_pmid(doi)
        if resolved_pmid:
            xml = _get_fulltext_xml("MED", resolved_pmid)
            if xml:
                return xml
        resolved_pmcid = doi_to_pmcid(doi)
        if resolved_pmcid:
            bare = resolved_pmcid.replace("PMC", "")
            return _get_fulltext_xml("PMC", bare)

    return None


def _get_fulltext_xml(source: str, identifier: str) -> bytes | None:
    url = f"{EUROPE_PMC_BASE}/{source}/{identifier}/fullTextXML"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None

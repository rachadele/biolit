"""PubMed / PubMed Central fetchers using NCBI E-utilities."""
import os
import threading
import time
import xml.etree.ElementTree as ET

import requests

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
IDCONV_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

_MAX_RETRIES = 3

# Global (cross-thread) rate gate. NCBI allows 3 req/s anonymously and 10/s
# with an API key. A bare time.sleep after each call only paces the calling
# thread — under a thread pool, N threads each pace themselves independently
# and the AGGREGATE request rate blows past NCBI's ceiling and trips 429s,
# regardless of whether an API key is set. This lock serializes request
# *timing* across every caller in the process so the aggregate stays under
# the (key-aware) ceiling; the HTTP itself still runs concurrently.
_RATE_LOCK = threading.Lock()
_LAST_REQ = 0.0


def _rate_interval() -> float:
    return (1.0 / 9.0) if os.environ.get("NCBI_API_KEY") else (1.0 / 2.5)


def _throttle() -> None:
    global _LAST_REQ
    with _RATE_LOCK:
        now = time.monotonic()
        wait = _LAST_REQ + _rate_interval() - now
        if wait > 0:
            time.sleep(wait)
        _LAST_REQ = time.monotonic()


def _ncbi_params(**kwargs) -> dict:
    """Inject NCBI API key when available."""
    params = dict(**kwargs)
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key
    return params


def _ncbi_get(url: str, params: dict, *, timeout: float) -> requests.Response:
    """GET an NCBI endpoint with global throttling and retry-with-backoff on
    429 / 5xx. Raises ``requests.exceptions.HTTPError`` on a non-retryable or
    retry-exhausted failure — same contract as ``requests.get(...).raise_for_status()``,
    just with retries in between."""
    for attempt in range(_MAX_RETRIES + 1):
        _throttle()
        resp = requests.get(url, params=params, timeout=timeout)
        try:
            resp.raise_for_status()
            return resp
        except requests.exceptions.HTTPError:
            if attempt < _MAX_RETRIES and (resp.status_code == 429 or 500 <= resp.status_code < 600):
                retry_after = resp.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after else 2 ** attempt)
                continue
            raise


def fetch_pubmed_metadata(pmid: str) -> dict | None:
    """Fetch title, abstract, MeSH terms, and DOI for a PMID."""
    resp = _ncbi_get(
        f"{NCBI_BASE}efetch.fcgi",
        _ncbi_params(db="pubmed", id=pmid, rettype="xml", retmode="xml"),
        timeout=15,
    )
    root = ET.fromstring(resp.content)
    article = root.find(".//PubmedArticle")
    if article is None:
        return None

    title_elem = article.find(".//ArticleTitle")
    title = "".join(title_elem.itertext()).strip() if title_elem is not None else ""
    abstract_parts = article.findall(".//AbstractText")
    abstract = " ".join(
        stripped for t in abstract_parts if (stripped := "".join(t.itertext()).strip())
    )
    mesh_terms = [
        m.findtext("DescriptorName", default="")
        for m in article.findall(".//MeshHeading")
    ]
    # Try to extract DOI
    doi = None
    for id_elem in article.findall(".//ArticleId"):
        if id_elem.get("IdType") == "doi":
            doi = id_elem.text
            break

    # Extract authors: "LastName Initials" or CollectiveName for consortia
    author_parts = []
    for author in article.findall(".//Author"):
        collective = author.findtext("CollectiveName")
        if collective:
            author_parts.append(collective.strip())
        else:
            last = author.findtext("LastName") or ""
            initials = author.findtext("Initials") or ""
            name = f"{last} {initials}".strip()
            if name:
                author_parts.append(name)
    authors = ", ".join(author_parts) if author_parts else None

    # Journal + publication year (curator context: venue + when it appeared).
    journal = (article.findtext(".//Journal/Title")
               or article.findtext(".//Journal/ISOAbbreviation") or "").strip() or None
    year = (article.findtext(".//JournalIssue/PubDate/Year")
            or article.findtext(".//PubDate/Year")
            or (article.findtext(".//PubDate/MedlineDate") or "")[:4] or None)

    # Author affiliations — the submitting lab's institution is a strong signal
    # for confirming a dataset↔paper match. Dedup in document order; expose the
    # full list plus a primary institution (first affiliation).
    affiliations: list[str] = []
    for aff in article.findall(".//AffiliationInfo/Affiliation"):
        t = (aff.text or "").strip()
        if t and t not in affiliations:
            affiliations.append(t)
    institution = affiliations[0] if affiliations else None

    return {
        "pmid": pmid,
        "doi": doi,
        "title": title,
        "abstract": abstract,
        "mesh_terms": mesh_terms,
        "authors": authors,
        "journal": journal,
        "year": year,
        "affiliations": affiliations,
        "institution": institution,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "fulltext_xml": None,
        "fulltext_pdf": None,
    }


def _idconv_lookup(accession: str) -> dict:
    """Call the NCBI ID Converter for any accession (PMID, DOI, PMCID).

    Returns the first record dict, or {} on failure.
    """
    try:
        resp = _ncbi_get(IDCONV_URL, {"ids": accession, "format": "json"}, timeout=10)
        records = resp.json().get("records", [])
        return records[0] if records else {}
    except Exception:
        return {}


def pmid_to_pmcid(pmid: str) -> str | None:
    """Convert a PMID to a PMCID via the NCBI ID Converter API."""
    return _idconv_lookup(pmid).get("pmcid")


def doi_to_pmcid(doi: str) -> str | None:
    """Convert a DOI to a PMCID via the NCBI ID Converter API."""
    return _idconv_lookup(doi).get("pmcid")


def doi_to_pmid(doi: str) -> str | None:
    """Convert a DOI to a PMID via PubMed esearch."""
    try:
        resp = _ncbi_get(
            f"{NCBI_BASE}esearch.fcgi",
            _ncbi_params(db="pubmed", term=f"{doi}[doi]", retmode="json"),
            timeout=10,
        )
        ids = resp.json().get("esearchresult", {}).get("idlist", [])
        return str(ids[0]) if ids else None
    except Exception:
        return None


def fetch_pmc_fulltext(pmid: str) -> bytes | None:
    """Return JATS XML bytes for a PMID from PubMed Central, or None if unavailable."""
    pmcid = pmid_to_pmcid(pmid)
    if not pmcid:
        return None
    numeric_id = pmcid.replace("PMC", "")
    try:
        resp = _ncbi_get(
            f"{NCBI_BASE}efetch.fcgi",
            _ncbi_params(db="pmc", id=numeric_id, rettype="xml", retmode="xml"),
            timeout=30,
        )
        return resp.content
    except Exception:
        return None

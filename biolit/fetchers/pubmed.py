"""PubMed / PubMed Central fetchers using NCBI E-utilities."""
import os
import time
import xml.etree.ElementTree as ET

import requests

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
IDCONV_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

_RATE_DELAY = 0.4  # seconds between NCBI requests (unauthenticated = 3/s max)


def _ncbi_params(**kwargs) -> dict:
    """Inject NCBI API key when available."""
    params = dict(**kwargs)
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key
    return params


def fetch_pubmed_metadata(pmid: str) -> dict | None:
    """Fetch title, abstract, MeSH terms, and DOI for a PMID."""
    resp = requests.get(
        f"{NCBI_BASE}efetch.fcgi",
        params=_ncbi_params(db="pubmed", id=pmid, rettype="xml", retmode="xml"),
        timeout=15,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    article = root.find(".//PubmedArticle")
    if article is None:
        return None

    title = article.findtext(".//ArticleTitle", default="").strip()
    abstract_parts = article.findall(".//AbstractText")
    abstract = " ".join((t.text or "").strip() for t in abstract_parts if t.text)
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

    time.sleep(_RATE_DELAY)
    return {
        "pmid": pmid,
        "doi": doi,
        "title": title,
        "abstract": abstract,
        "mesh_terms": mesh_terms,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "fulltext_xml": None,
        "fulltext_pdf": None,
    }


def _idconv_lookup(accession: str) -> dict:
    """Call the NCBI ID Converter for any accession (PMID, DOI, PMCID).

    Returns the first record dict, or {} on failure.
    """
    try:
        resp = requests.get(
            IDCONV_URL,
            params={"ids": accession, "format": "json"},
            timeout=10,
        )
        resp.raise_for_status()
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
        resp = requests.get(
            f"{NCBI_BASE}esearch.fcgi",
            params=_ncbi_params(db="pubmed", term=f"{doi}[doi]", retmode="json"),
            timeout=10,
        )
        resp.raise_for_status()
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
        resp = requests.get(
            f"{NCBI_BASE}efetch.fcgi",
            params=_ncbi_params(db="pmc", id=numeric_id, rettype="xml", retmode="xml"),
            timeout=30,
        )
        resp.raise_for_status()
        time.sleep(_RATE_DELAY)
        return resp.content
    except Exception:
        return None


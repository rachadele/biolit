"""Fetcher for NCBI GEO series records (GSE accessions) via the MINiML XML API."""
import time
import xml.etree.ElementTree as ET

import requests

GEO_MINIML_URL = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"
_RATE_DELAY = 0.4


def fetch_geo_record(accession: str) -> dict | None:
    """Fetch a GEO series record and return a paper-shaped dict.

    Fetches MINiML XML for *accession* (e.g. ``GSE12345``) and extracts:
    title, summary, overall design, experiment type, organism, and any
    linked PubMed IDs.

    Returns a dict compatible with the pipeline's ``paper`` format so the
    same LLM screening and extraction calls work unchanged.
    """
    try:
        resp = requests.get(
            GEO_MINIML_URL,
            params={"acc": accession, "targ": "self", "form": "xml", "view": "brief"},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"GEO fetch failed for {accession}: {e}") from e

    time.sleep(_RATE_DELAY)
    return _parse_miniml(accession, resp.content)


def _parse_miniml(accession: str, xml_bytes: bytes) -> dict | None:
    """Parse a GEO MINiML XML response into a paper-shaped dict."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    # MINiML uses a namespace; strip it for simpler findtext calls
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    def _text(elem, tag: str, default: str = "") -> str:
        node = elem.find(f"{ns}{tag}")
        return (node.text or "").strip() if node is not None else default

    def _all_text(elem, tag: str) -> list[str]:
        return [(n.text or "").strip() for n in elem.findall(f"{ns}{tag}") if n.text]

    series = root.find(f"{ns}Series")
    if series is None:
        return None

    title = _text(series, "Title")
    summary = _text(series, "Summary")
    overall_design = _text(series, "Overall-Design")
    experiment_type = _text(series, "Type")
    pmids = _all_text(series, "Pubmed-ID")

    # Collect organism from child Sample elements if not on Series directly
    organisms = list({
        _text(s, "Organism") or _text(s, "organism")
        for s in root.findall(f".//{ns}Sample")
    } - {""})

    # Build an abstract-like blob from the structured fields
    abstract_parts = []
    if summary:
        abstract_parts.append(f"Summary: {summary}")
    if overall_design:
        abstract_parts.append(f"Overall design: {overall_design}")
    if experiment_type:
        abstract_parts.append(f"Experiment type: {experiment_type}")
    if organisms:
        abstract_parts.append(f"Organism(s): {', '.join(organisms)}")
    abstract = "\n\n".join(abstract_parts)

    return {
        "pmid": pmids[0] if pmids else None,
        "accession": accession,
        "doi": None,
        "title": title,
        "abstract": abstract,
        "mesh_terms": ([experiment_type] if experiment_type else []) + organisms,
        "url": f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession}",
        "pmids": pmids,
        "text_source": "geo_record",
    }

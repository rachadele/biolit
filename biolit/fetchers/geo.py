"""Fetcher for NCBI GEO series records (GSE accessions) via the MINiML XML API."""
import time
import xml.etree.ElementTree as ET

import requests

GEO_MINIML_URL = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"
_RATE_DELAY = 0.4


def fetch_geo_record(accession: str) -> dict | None:
    """Fetch a GEO series record and return a paper-shaped dict.

    Fetches MINiML XML for *accession* (e.g. ``GSE12345``) with targ=all to
    include Platform elements. Extracts: title, summary, overall design,
    experiment type, organism, platform(s), sample count, and linked PubMed IDs.

    Returns a dict compatible with the pipeline's ``paper`` format so the
    same LLM screening and extraction calls work unchanged.
    """
    try:
        resp = requests.get(
            GEO_MINIML_URL,
            params={"acc": accession, "targ": "all", "form": "xml", "view": "brief"},
            timeout=60,
        )
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"GEO fetch failed for {accession}: {e}") from e

    time.sleep(_RATE_DELAY)
    record = _parse_miniml(accession, resp.content)
    if record is not None:
        record["geo_metadata_text"] = format_geo_metadata(record)
    return record


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
    sample_count = len(series.findall(f"{ns}Sample-Ref"))

    # Build a name index from top-level Contributor elements, then resolve
    # the iid references listed inside the Series element.
    contributor_index: dict[str, str] = {}
    for contrib in root.findall(f"{ns}Contributor"):
        iid = contrib.get("iid", "")
        person = contrib.find(f"{ns}Person")
        if person is not None:
            first = (person.findtext(f"{ns}First") or "").strip()
            last = (person.findtext(f"{ns}Last") or "").strip()
            name = f"{last} {first}".strip() if last else first
        else:
            # Fallback: Organisation name
            name = (contrib.findtext(f"{ns}Organization") or "").strip()
        if iid and name:
            contributor_index[iid] = name

    author_parts = []
    for ref in series.findall(f"{ns}Contributor"):
        iid = ref.get("iid", "")
        name = contributor_index.get(iid)
        if name:
            author_parts.append(name)
    authors = ", ".join(author_parts) if author_parts else None

    # Parse Platform elements (only present with targ=all)
    platforms = []
    for plat in root.findall(f"{ns}Platform"):
        gpl = _text(plat, "Accession")
        plat_title = _text(plat, "Title")
        technology = _text(plat, "Technology")
        plat_organism = _text(plat, "Organism")
        if gpl:
            platforms.append({
                "accession": gpl,
                "title": plat_title,
                "technology": technology,
                "organism": plat_organism,
            })

    # Collect organisms from Platform elements first, fall back to Sample elements
    organisms = list({p["organism"] for p in platforms if p["organism"]})
    if not organisms:
        organisms = list({
            _text(s, "Organism") or _text(s, "organism")
            for s in root.findall(f".//{ns}Sample")
        } - {""})

    # Build an abstract-like blob from the structured fields (used for screening)
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
        "authors": authors,
        "url": f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession}",
        "pmids": pmids,
        "platforms": platforms,
        "organisms": organisms,
        "sample_count": sample_count,
        "text_source": "geo_record",
    }


def format_geo_metadata(record: dict) -> str:
    """Format parsed GEO record fields as clean labeled text for the LLM.

    Produces a compact, human-readable block covering accession, type,
    organism(s), platform(s), sample count, linked PMIDs, summary, and
    overall design. Replaces the raw MINiML XML that was previously appended
    to the LLM context.
    """
    lines = [f"=== GEO Metadata: {record.get('accession', '')} ==="]

    if record.get("title"):
        lines.append(f"Title: {record['title']}")

    # Experiment type (from mesh_terms or abstract)
    mesh = record.get("mesh_terms", [])
    if mesh:
        lines.append(f"Type: {mesh[0]}")

    organisms = record.get("organisms", [])
    if organisms:
        lines.append(f"Organism(s): {', '.join(organisms)}")

    platforms = record.get("platforms", [])
    if platforms:
        plat_strs = []
        for p in platforms:
            parts = [p["accession"]]
            if p.get("title"):
                parts.append(p["title"])
            if p.get("technology"):
                parts.append(f"[{p['technology']}]")
            plat_strs.append(" — ".join(parts))
        lines.append(f"Platform(s): {'; '.join(plat_strs)}")

    if record.get("sample_count"):
        lines.append(f"Sample count: {record['sample_count']}")

    pmids = record.get("pmids", [])
    if pmids:
        lines.append(f"Linked PMID(s): {', '.join(pmids)}")

    # Summary and Overall Design (from the abstract blob or re-extracted)
    abstract = record.get("abstract", "")
    for part in abstract.split("\n\n"):
        if part.strip():
            lines.append("")
            lines.append(part.strip())

    return "\n".join(lines)

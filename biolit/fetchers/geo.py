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
    # view=quick returns the full per-Sample blocks (title, source_name,
    # Channel/Characteristics, treatment/growth/extract protocols,
    # instrument model) AT NEGLIGIBLE BANDWIDTH COST relative to view=brief
    # (a 21-sample series is ~85 KB at quick vs ~10 KB at brief; both
    # are dwarfed by a paper full-text fetch). The previous view=brief
    # only returned <Sample-Ref iid="…"/> stubs, so per-sample data was
    # never recoverable — caught 2026-05-30 on GSE304359, where the
    # downstream gemma-curation-agents proposer was missing per-sample
    # genotype/treatment varying values that would have rescued a 2×2
    # design. See gemma-curation-agents notable_cases.md "GSE304359 —
    # gold-cap failure on a 2×2 design".
    try:
        resp = requests.get(
            GEO_MINIML_URL,
            params={"acc": accession, "targ": "all", "form": "xml", "view": "quick"},
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
    # Series references its contributors as <Contributor-Ref ref="contribN"/>
    # (older/variant MINiML may inline <Contributor iid="…"/> — handle both,
    # by ``ref`` then ``iid``). The prior code looked for <Contributor> with
    # ``iid`` inside <Series>, which never matched, so ``authors`` was always
    # None — fixed here.
    refs = (series.findall(f"{ns}Contributor-Ref")
            or series.findall(f"{ns}Contributor"))
    for ref in refs:
        key = ref.get("ref") or ref.get("iid") or ""
        name = contributor_index.get(key)
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

    # Per-sample blocks — only populated when MINiML was fetched at
    # view=quick (or higher). Each element is a dict carrying title,
    # source_name, organism, characteristics (a tag→value dict),
    # treatment/growth/extract protocols, library/instrument metadata
    # when present. Downstream curation pipelines (gemma-curation-agents
    # preboarding) consume these to drive the design-proposer's
    # per-sample MINiML rendering — without this list the proposer
    # operates blind to sample-name patterns the curator relies on.
    samples = _parse_samples(root, ns)

    # --- Series-level metadata beyond the paper projection ----------------
    # The MINiML we already fetched carries dates, cross-references, and
    # supplementary-file links. Extract them here so downstream consumers
    # (e.g. gemma-curation-agents preboarding, the pub-finder date check)
    # don't re-fetch the same XML just to read one more element. Add fields
    # here rather than in a second request the next time one is needed.
    status = series.find(f"{ns}Status")
    release_date = submission_date = last_update_date = None
    if status is not None:
        release_date = (status.findtext(f"{ns}Release-Date") or "").strip() or None
        submission_date = (status.findtext(f"{ns}Submission-Date") or "").strip() or None
        last_update_date = (status.findtext(f"{ns}Last-Update-Date") or "").strip() or None

    supplementary_files = []
    for sd in series.findall(f"{ns}Supplementary-Data"):
        url = (sd.text or "").strip()
        if url:
            supplementary_files.append(
                {"type": (sd.get("type") or "").strip(), "url": url})

    # Cross-references — BioProject / SRA / dbGaP / re-analysis links.
    relations = []
    for rel in series.findall(f"{ns}Relation"):
        target = (rel.get("target") or "").strip()
        if target:
            relations.append(
                {"type": (rel.get("type") or "").strip(), "target": target})

    # Submitting organisation(s): top-level <Contributor> elements that
    # carry an <Organization> rather than a <Person>.
    organizations = []
    for contrib in root.findall(f"{ns}Contributor"):
        if contrib.find(f"{ns}Person") is None:
            org = (contrib.findtext(f"{ns}Organization") or "").strip()
            if org:
                organizations.append(org)

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
        "samples": samples,
        "text_source": "geo_record",
        # Series-level metadata (added 2026-07-04 — capture the whole record,
        # not just the paper-shaped projection):
        "release_date": release_date,
        "submission_date": submission_date,
        "last_update_date": last_update_date,
        "summary": summary,
        "overall_design": overall_design,
        "experiment_type": experiment_type,
        "supplementary_files": supplementary_files,
        "relations": relations,
        "organizations": organizations,
    }


def _parse_samples(root, ns: str) -> list[dict]:
    """Extract per-Sample records from MINiML XML.

    Each Sample is one dict carrying whichever fields are populated.
    Keys mirror the namespace-stripped tag names where straightforward
    (``title``, ``source_name``, ``organism``, ``description``,
    ``treatment_protocol``, ``growth_protocol``, ``extract_protocol``,
    ``library_strategy``, ``library_source``, ``library_selection``,
    ``instrument_model``). Per-channel Characteristics elements are
    folded into a single ``characteristics`` dict keyed by their
    ``tag`` attribute; for two-channel arrays, channel-2 values get
    a ``ch2_`` key prefix to keep both channels visible.

    Returns ``[]`` when the response has no ``<Sample>`` elements
    (e.g. when MINiML was fetched at ``view=brief`` — only
    Sample-Ref pointers come through).
    """
    samples_out: list[dict] = []
    for s in root.findall(f"{ns}Sample"):
        accession = (s.get("iid") or "").strip()
        if not accession:
            continue
        rec: dict = {"accession": accession}

        # Simple top-level fields. Each is optional; only populate
        # when present so downstream consumers can do .get() safely.
        for tag, key in (
            ("Title", "title"),
            ("Source", "source_name"),
            ("Organism", "organism"),
            ("Description", "description"),
            ("Instrument-Model", "instrument_model"),
            ("Library-Strategy", "library_strategy"),
            ("Library-Source", "library_source"),
            ("Library-Selection", "library_selection"),
            ("Type", "sample_type"),
        ):
            v = (s.findtext(f"{ns}{tag}") or "").strip()
            if v:
                rec[key] = v

        # Protocols live at the Channel level in MINiML. Walk each
        # channel; collect characteristics + protocols. Channel 1 is
        # the default; channel 2 (two-colour arrays) gets a ch2_
        # prefix on its keys so both channels survive into rec.
        channels = list(s.findall(f"{ns}Channel"))
        characteristics: dict[str, str] = {}
        for ch in channels:
            ch_pos = (ch.get("position") or "1").strip()
            prefix = "" if ch_pos in ("", "1") else f"ch{ch_pos}_"

            for tag, key in (
                ("Source", "source_name"),
                ("Organism", "organism"),
                ("Treatment-Protocol", "treatment_protocol"),
                ("Growth-Protocol", "growth_protocol"),
                ("Extract-Protocol", "extract_protocol"),
                ("Molecule", "molecule"),
            ):
                v = (ch.findtext(f"{ns}{tag}") or "").strip()
                if v:
                    rec.setdefault(f"{prefix}{key}", v)

            for c in ch.findall(f"{ns}Characteristics"):
                tag = (c.get("tag") or "").strip()
                val = (c.text or "").strip()
                if tag and val:
                    characteristics[f"{prefix}{tag}"] = val

        if characteristics:
            rec["characteristics"] = characteristics

        samples_out.append(rec)
    return samples_out


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

    if record.get("release_date"):
        rel = f"Public release date: {record['release_date']}"
        if record.get("submission_date"):
            rel += f" (submitted {record['submission_date']})"
        lines.append(rel)

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

"""Pipeline orchestration: fetch → full-text → parse → screen → extract → CSV."""
import csv
import json
import os
import sys
from datetime import datetime

from biolit.fetchers._hooks import FetchContext, run_registered_fetchers
from biolit.fetchers.geo import fetch_geo_record
from biolit.fetchers.pubmed import fetch_pubmed_metadata, fetch_pmc_fulltext, doi_to_pmid
from biolit.fetchers.europepmc import fetch_europepmc_fulltext
from biolit.fetchers.preprints import fetch_preprint, fetch_preprint_metadata
from biolit.fetchers.unpaywall import fetch_via_unpaywall
from biolit.fetchers.semantic_scholar import fetch_s2_pdf, get_citation_count
from biolit.llm.base import BaseLLMClient
from biolit.parsers.jats import parse_jats_sections
from biolit.parsers.pdf import parse_pdf_sections
from biolit.parsers.utils import select_sections, DEFAULT_MAX_TOKENS
from biolit.utils import parse_json_response


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def build_output_schema(client: BaseLLMClient, fields_description: str | dict) -> dict:
    """Translate a field spec into a schema dict.

    *fields_description* can be:
    - a dict mapping field names to extraction descriptions (used as-is, no LLM call)
    - a comma-separated string of field names (LLM infers descriptions)
    """
    if isinstance(fields_description, dict):
        return fields_description

    prompt = (
        f"Convert this list of field names into a JSON object where each key is a field name "
        f"and each value is a clear description of what to extract from a scientific paper.\n\n"
        f"Fields: {fields_description}\n\n"
        f'Respond with valid JSON only, no other text. Example format:\n'
        f'{{"methodology": "general experimental method used (e.g. GWAS, WGS, scRNA-seq)", '
        f'"sample_type": "tissue type and biological origin of samples"}}'
    )
    response = client.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=500,
    )
    return parse_json_response(response)


def screen_paper(client: BaseLLMClient, paper: dict, criterion: str, text: str) -> dict:
    """Ask the LLM whether *paper* meets *criterion* given *text* as evidence."""
    prompt = (
        f"You are screening a scientific paper for relevance to a literature review.\n\n"
        f"Criterion: {criterion}\n\n"
        f"Title: {paper['title']}\n"
        f"MeSH terms: {', '.join(paper.get('mesh_terms', []))}\n\n"
        f"Paper content:\n{text}\n\n"
        f"Respond with valid JSON only, no other text:\n"
        f'{{"relevant": true or false, "reason": "one sentence"}}'
    )
    response = client.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=256,
    )
    return parse_json_response(response)


def extract_fields(client: BaseLLMClient, paper: dict, output_schema: dict, text: str, extraction_max_tokens: int = 4096) -> dict:
    """Extract structured fields from *paper* using the LLM."""
    schema_str = json.dumps(output_schema, indent=2)
    prompt = (
        f"Extract structured information from this paper.\n"
        f"Use only what is stated in the paper content — do not speculate.\n\n"
        f"Title: {paper['title']}\n"
        f"MeSH terms: {', '.join(paper.get('mesh_terms', []))}\n\n"
        f"Paper content:\n{text}\n\n"
        f"Extract the following fields. Respond with valid JSON only, no other text.\n"
        f"Fields to extract:\n{schema_str}\n\n"
        f"Return a JSON object with exactly those keys. Use null for fields not determinable."
    )
    response = client.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=extraction_max_tokens,
    )
    result = parse_json_response(response)
    result["title"] = paper["title"]
    result["authors"] = paper.get("authors")
    result["url"] = paper.get("url")
    result["pmid"] = paper.get("pmid")
    result["doi"] = paper.get("doi")
    result["geo_accession"] = paper.get("geo_accession")
    result["text_source"] = paper.get("text_source", "abstract")
    return result


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

_MARKDOWN_META_KEYS = frozenset({
    "title", "authors", "url", "pmid", "doi", "geo_accession", "text_source",
    "citation_count", "linked_pmids", "_stub", "_stub_reason",
})


def format_record_markdown(
    client: BaseLLMClient,
    record: dict,
    output_schema: dict | None,
    markdown_max_tokens: int = 1024,
) -> str:
    """Render a single record as a markdown section.

    Stub records (``_stub=True``) get a minimal header + failure note with no
    LLM call. Normal records get an LLM-generated prose section that is aware
    of the full output schema (field names and descriptions) so it can adapt
    its tone and gracefully omit null fields.
    """
    title = record.get("title") or "Unknown"
    url = record.get("url", "")
    pmid = record.get("pmid")
    doi = record.get("doi")
    geo = record.get("geo_accession")
    text_source = record.get("text_source", "")
    citations = record.get("citation_count")

    authors = record.get("authors")

    # Build metadata header lines
    meta_lines = []
    if authors:
        meta_lines.append(f"**Authors:** {authors}")
    if url:
        meta_lines.append(f"**URL:** {url}")
    if pmid:
        meta_lines.append(f"**PMID:** {pmid}")
    if doi:
        meta_lines.append(f"**DOI:** {doi}")
    if geo:
        meta_lines.append(f"**GEO:** {geo}")
    if text_source:
        meta_lines.append(f"**Text source:** {text_source}")
    if citations is not None:
        meta_lines.append(f"**Citations:** {citations}")

    header = f"## {title}\n" + "\n".join(meta_lines)

    # Stub: no LLM call needed
    if record.get("_stub"):
        reason = record.get("_stub_reason", "unknown error")
        return f"{header}\n\n_Record could not be fully processed: {reason}._\n"

    # Metadata-only run (no extraction schema): no LLM call needed
    if not output_schema:
        return f"{header}\n"

    # Full LLM render: pass schema + extracted values so the model has context
    extracted = {k: v for k, v in record.items() if k not in _MARKDOWN_META_KEYS}
    schema_str = json.dumps(output_schema, indent=2)
    extracted_str = json.dumps(extracted, indent=2)

    prompt = (
        "You are writing a structured markdown summary section for an academic literature review.\n\n"
        f"Paper metadata:\n"
        f"  Title: {title}\n"
        f"  PMID: {pmid}\n"
        f"  DOI: {doi}\n"
        f"  Text source: {text_source}\n\n"
        f"Field schema (what each field means):\n{schema_str}\n\n"
        f"Extracted field values:\n{extracted_str}\n\n"
        "Write the body of the markdown section for this paper. Rules:\n"
        "- Do NOT include the paper title or metadata (those are prepended separately)\n"
        "- Write one ### subsection per extracted field, in clean natural prose\n"
        "- Silently omit fields whose value is null, empty, or not applicable\n"
        "- Do not copy values verbatim — adapt them into readable sentences\n"
        "- Keep each subsection concise (1-3 sentences)\n"
        "- Do not add any preamble or closing remarks"
    )
    body = client.chat([{"role": "user", "content": prompt}], max_tokens=markdown_max_tokens)
    return f"{header}\n\n{body}\n"


def generate_markdown_summary(
    client: BaseLLMClient,
    records: list[dict],
    output_schema: dict | None,
    markdown_max_tokens: int = 1024,
) -> str:
    """Render all records (including stubs) as a single markdown document."""
    sections = ["# Literature Search Results\n"]
    for record in records:
        sections.append(format_record_markdown(client, record, output_schema, markdown_max_tokens))
    return "\n---\n\n".join(sections)


# ---------------------------------------------------------------------------
# Input type detection and unified record fetching
# ---------------------------------------------------------------------------

def _detect_id_type(id_str: str) -> str:
    """Return 'geo', 'doi', or 'pmid' based on the identifier string."""
    s = id_str.strip()
    if s.upper().startswith(("GSE", "GDS", "GSM", "GPL")):
        return "geo"
    if s.startswith("10.") or (s.startswith("10") and "/" in s):
        return "doi"
    return "pmid"


def fetch_record(id_str: str) -> dict | None:
    """Fetch a normalized record dict for a PMID, DOI, or GEO accession.

    All returned dicts include 'pmid', 'doi', and 'geo_accession' keys
    (None when not applicable) so they can flow through the same
    screen / extract / CSV pipeline.
    """
    id_str = id_str.strip()
    id_type = _detect_id_type(id_str)

    if id_type == "geo":
        record = fetch_geo_record(id_str)
        if record is None:
            return None
        record["geo_accession"] = id_str
        record.setdefault("pmid", None)
        record.setdefault("doi", None)
        return record

    if id_type == "doi":
        # Try to resolve to a PMID for full PubMed metadata + full-text chain.
        pmid = doi_to_pmid(id_str)
        if pmid:
            paper = fetch_pubmed_metadata(pmid)
            if paper:
                paper["geo_accession"] = None
                return paper
        # Preprint or unresolvable DOI — use preprint API for title/abstract.
        meta = fetch_preprint_metadata(id_str)
        return {
            "title": meta.get("title", id_str) if meta else id_str,
            "abstract": meta.get("abstract", "") if meta else "",
            "doi": id_str,
            "pmid": None,
            "geo_accession": None,
            "url": f"https://doi.org/{id_str}",
            "mesh_terms": [],
            "authors": meta.get("authors") if meta else None,
        }

    # PMID
    paper = fetch_pubmed_metadata(id_str)
    if paper is None:
        return None
    paper["geo_accession"] = None
    return paper


# ---------------------------------------------------------------------------
# Full-text resolution
# ---------------------------------------------------------------------------

def resolve_fulltext(
    paper: dict,
    unpaywall_email: str | None = None,
    sections_wanted: list[str] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> tuple[str, str, dict]:
    """Attempt to fetch full text for *paper*, returning (text, source_label, artifacts).

    Fallback chain:
      1. PMC JATS XML (via NCBI efetch) — skipped when pmid is None
      2. Europe PMC JATS XML (broader open-access coverage)
      3. Preprint JATS XML (bioRxiv / medRxiv)
      4. Unpaywall PDF
      5. Semantic Scholar open-access PDF
      6. Abstract only

    *sections_wanted* filters which sections are concatenated (None = all).

    Custom fetchers registered via :func:`biolit.fetchers.register_fetcher`
    are tried before the built-in chain (Zotero, on-disk PDFs, etc.). See
    :mod:`biolit.fetchers._hooks` for the protocol.
    """
    artifacts: dict = {}

    # Custom-fetcher prepended chain. The result's ``source`` is whatever
    # the custom fetcher chose (e.g. ``zotero_pdf``, ``local_pdf``); its
    # ``artifacts`` dict is merged into the per-paper artifacts directory
    # exactly like the built-in PMC/EuropePMC bytes.
    custom = run_registered_fetchers(
        FetchContext(
            paper=paper,
            unpaywall_email=unpaywall_email,
            sections_wanted=sections_wanted,
            max_tokens=max_tokens,
        )
    )
    if custom is not None and custom.text:
        artifacts.update(custom.artifacts)
        return custom.text, custom.source, artifacts
    if custom is not None:
        artifacts.update(custom.artifacts)

    pmid = paper.get("pmid")
    doi = paper.get("doi")

    # 1. PMC (NCBI efetch) — requires a PMID
    if pmid:
        xml_bytes = fetch_pmc_fulltext(pmid)
        if xml_bytes:
            artifacts["pmc_xml"] = xml_bytes
            secs = parse_jats_sections(xml_bytes)
            if secs:
                return select_sections(secs, sections_wanted, max_tokens), "pmc_fulltext", artifacts

    # 2. Europe PMC MED/{pmid} — broader open-access coverage beyond NCBI PMC
    xml_bytes = fetch_europepmc_fulltext(pmid=pmid, doi=doi)
    if xml_bytes:
        artifacts["europepmc_xml"] = xml_bytes
        secs = parse_jats_sections(xml_bytes)
        if secs:
            return select_sections(secs, sections_wanted, max_tokens), "europepmc_fulltext", artifacts

    # 3. Preprints
    if doi:
        xml_bytes = fetch_preprint(doi)
        if xml_bytes:
            artifacts["preprint_xml"] = xml_bytes
            secs = parse_jats_sections(xml_bytes)
            if secs:
                return select_sections(secs, sections_wanted, max_tokens), "preprint_fulltext", artifacts

    # 4. Unpaywall PDF
    if doi and unpaywall_email:
        pdf_bytes = fetch_via_unpaywall(doi, unpaywall_email)
        if pdf_bytes:
            artifacts["unpaywall_pdf"] = pdf_bytes
            try:
                secs = parse_pdf_sections(pdf_bytes)
                if secs:
                    return select_sections(secs, sections_wanted, max_tokens), "unpaywall_pdf", artifacts
            except ImportError:
                print("  [warning] pdfminer.six not installed; skipping PDF parsing", file=sys.stderr)

    # 5. Semantic Scholar open-access PDF
    if doi:
        pdf_bytes = fetch_s2_pdf(doi)
        if pdf_bytes:
            artifacts["s2_pdf"] = pdf_bytes
            try:
                secs = parse_pdf_sections(pdf_bytes)
                if secs:
                    return select_sections(secs, sections_wanted, max_tokens), "s2_pdf", artifacts
            except ImportError:
                print("  [warning] pdfminer.six not installed; skipping PDF parsing", file=sys.stderr)

    # 6. Abstract fallback
    # For DOI-only preprints, fetch_record() already populated paper["abstract"]
    # from the preprint API, so this covers both PubMed and preprint cases.
    return paper.get("abstract", ""), "abstract", artifacts


# ---------------------------------------------------------------------------
# Single-record convenience functions
# ---------------------------------------------------------------------------

def screen_by_pmid(
    client: BaseLLMClient,
    pmid: str,
    criterion: str,
    unpaywall_email: str | None = None,
) -> dict:
    """Fetch a PubMed paper and screen it for relevance in one call."""
    paper = fetch_pubmed_metadata(pmid)
    if paper is None:
        return {"error": f"No record found for PMID {pmid}"}

    text, source, _ = resolve_fulltext(paper, unpaywall_email=unpaywall_email)
    result = screen_paper(client, paper, criterion, text)
    result["text_source"] = source
    return result


def screen_by_doi(
    client: BaseLLMClient,
    doi: str,
    criterion: str,
    unpaywall_email: str | None = None,
) -> dict:
    """Fetch a paper by DOI and screen it for relevance.

    Used when a DOI cannot be resolved to a PMID (e.g. preprints).
    """
    text = ""
    source = "unavailable"

    xml_bytes = fetch_preprint(doi)
    if xml_bytes:
        secs = parse_jats_sections(xml_bytes)
        if secs:
            text = select_sections(secs, None, DEFAULT_MAX_TOKENS)
            source = "preprint_fulltext"

    if not text:
        xml_bytes = fetch_europepmc_fulltext(doi=doi)
        if xml_bytes:
            secs = parse_jats_sections(xml_bytes)
            if secs:
                text = select_sections(secs, None, DEFAULT_MAX_TOKENS)
                source = "europepmc_fulltext"

    if not text and unpaywall_email:
        pdf_bytes = fetch_via_unpaywall(doi, unpaywall_email)
        if pdf_bytes:
            try:
                secs = parse_pdf_sections(pdf_bytes)
                if secs:
                    text = select_sections(secs, None, DEFAULT_MAX_TOKENS)
                    source = "unpaywall_pdf"
            except ImportError:
                pass

    if not text:
        pdf_bytes = fetch_s2_pdf(doi)
        if pdf_bytes:
            try:
                secs = parse_pdf_sections(pdf_bytes)
                if secs:
                    text = select_sections(secs, None, DEFAULT_MAX_TOKENS)
                    source = "s2_pdf"
            except ImportError:
                pass

    title = doi
    if not text:
        meta = fetch_preprint_metadata(doi)
        if meta:
            text = meta.get("abstract", "")
            source = "preprint_abstract"
            title = meta.get("title", doi)

    if not text:
        return {"error": f"No content retrievable for DOI {doi}"}

    paper = {"title": title, "mesh_terms": []}
    result = screen_paper(client, paper, criterion, text)
    result["text_source"] = source
    result["doi"] = doi
    return result


def screen_by_geo(
    client: BaseLLMClient,
    accession: str,
    criterion: str,
) -> dict:
    """Fetch a GEO record and screen it for relevance in one call."""
    record = fetch_geo_record(accession)
    if record is None:
        return {"error": f"No record found for accession {accession}"}

    text = record.get("abstract", "")
    paper = {"title": record.get("title", accession), "mesh_terms": []}
    result = screen_paper(client, paper, criterion, text)
    result["text_source"] = "geo_metadata"
    return result


# ---------------------------------------------------------------------------
# GEO full-text helper
# ---------------------------------------------------------------------------

def _resolve_geo_fulltext(
    paper: dict,
    unpaywall_email: str | None = None,
    sections_wanted: list[str] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> tuple[str, str, dict]:
    """Attempt to fetch full text via linked PMIDs for a GEO record.

    Tries each linked PMID in order. Returns the first real full text found.
    Falls back to the first linked abstract, then to GEO metadata.

    Structured GEO metadata (platform, organism, summary, etc.) is always
    prepended to the returned text so the LLM has access to key GEO fields
    even when the main text comes from a linked publication.

    Returns (text, source_label, artifacts).
    """
    geo_metadata = paper.get("geo_metadata_text", "")
    geo_prefix = f"{geo_metadata}\n\n--- Linked Publication ---\n" if geo_metadata else ""

    first_linked_abstract: str | None = None
    first_linked_artifacts: dict = {}

    for pmid in paper.get("pmids", []):
        try:
            linked_paper = fetch_pubmed_metadata(pmid)
        except Exception:
            continue
        if linked_paper is None:
            continue

        # Propagate authors from the linked paper to the GEO record
        if paper.get("authors") is None and linked_paper.get("authors"):
            paper["authors"] = linked_paper["authors"]

        text, source, artifacts = resolve_fulltext(
            linked_paper, unpaywall_email, sections_wanted, max_tokens
        )
        if source != "abstract":
            return f"{geo_prefix}{text}", "geo_linked_fulltext", artifacts
        if first_linked_abstract is None and text:
            first_linked_abstract = text
            first_linked_artifacts = artifacts

    if first_linked_abstract:
        return f"{geo_prefix}{first_linked_abstract}", "geo_linked_abstract", first_linked_artifacts

    geo_text = paper.get("abstract", "")
    if len(geo_text) > max_tokens * 4:
        geo_text = geo_text[:max_tokens * 4]
    return geo_metadata or geo_text, "geo_record", {}


# ---------------------------------------------------------------------------
# Main unified pipeline
# ---------------------------------------------------------------------------

def run(
    client: BaseLLMClient,
    ids: list[str],
    criterion: str | None = None,
    fields_description: str | dict | None = None,
    output_path: str = "results.csv",
    unpaywall_email: str | None = None,
    sections_wanted: list[str] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    markdown: bool = False,
    markdown_max_tokens: int = 1024,
    extraction_max_tokens: int = 4096,
) -> tuple[str | None, int]:
    """Fetch, optionally screen, and optionally extract a mixed list of PMIDs, DOIs, and GEO accessions.

    Each identifier is auto-detected and routed to the appropriate fetcher.
    All record types flow through the same fetch → (screen) → (extract) → artifacts → CSV loop.

    - If *criterion* is None, the screening step is skipped and all records proceed to extraction.
    - If *fields_description* is None, the extraction step is skipped and only metadata columns
      (title, url, pmid, doi, geo_accession, text_source, citation_count) are written.
    - The output CSV always includes pmid, doi, and geo_accession columns.
    """
    output_schema: dict | None = None
    if fields_description:
        print("Building output schema...", file=sys.stderr)
        output_schema = build_output_schema(client, fields_description)
        print(f"  Fields: {', '.join(output_schema.keys())}\n", file=sys.stderr)

    print(f"Processing {len(ids)} identifiers\n", file=sys.stderr)

    run_dir, csv_path = _make_run_dir(output_path)
    artifacts_root = os.path.join(run_dir, "artifacts")
    os.makedirs(artifacts_root, exist_ok=True)
    print(f"Run directory: {run_dir}\n", file=sys.stderr)

    results = []
    stubs: list[dict] = []  # skipped/failed records; markdown-only, not written to CSV

    def _stub(reason: str, paper: dict | None = None, id_str: str = "") -> dict:
        """Build a stub entry for the markdown from a paper dict or bare id_str."""
        return {
            "title": (paper.get("title") if paper else None) or id_str,
            "url": paper.get("url") if paper else None,
            "pmid": paper.get("pmid") if paper else None,
            "doi": paper.get("doi") if paper else None,
            "geo_accession": paper.get("geo_accession") if paper else None,
            "text_source": paper.get("text_source") if paper else None,
            "_stub": True,
            "_stub_reason": reason,
        }

    for i, id_str in enumerate(ids, 1):
        id_type = _detect_id_type(id_str)
        print(f"[{i}/{len(ids)}] {id_str}", end=" ... ", flush=True, file=sys.stderr)

        try:
            paper = fetch_record(id_str)
        except Exception as e:
            print(f"fetch error: {e}", file=sys.stderr)
            stubs.append(_stub(f"fetch error: {e}", id_str=id_str))
            continue

        if not paper:
            print("skipped (not found)", file=sys.stderr)
            stubs.append(_stub("record not found", id_str=id_str))
            continue

        print("resolving full text...", end=" ", flush=True, file=sys.stderr)
        if id_type == "geo":
            text, source, fulltext_artifacts = _resolve_geo_fulltext(
                paper, unpaywall_email, sections_wanted, max_tokens
            )
        else:
            text, source, fulltext_artifacts = resolve_fulltext(
                paper, unpaywall_email, sections_wanted, max_tokens
            )
        paper["text_source"] = source

        # Persist artifacts so the run is reproducible and inspectable.
        slug_id = paper.get("geo_accession") or paper.get("pmid") or id_str
        paper_slug = f"{slug_id}_{_safe_name(paper.get('title', 'paper'))}"
        paper_dir = os.path.join(artifacts_root, paper_slug)
        os.makedirs(paper_dir, exist_ok=True)
        _write_text(os.path.join(paper_dir, "selected_text.txt"), text)
        _write_text(
            os.path.join(paper_dir, "metadata.json"),
            json.dumps(
                {
                    "pmid": paper.get("pmid"),
                    "doi": paper.get("doi"),
                    "geo_accession": paper.get("geo_accession"),
                    "title": paper.get("title"),
                    "url": paper.get("url"),
                    "mesh_terms": paper.get("mesh_terms", []),
                    "text_source": source,
                },
                indent=2,
            ),
        )
        _write_bytes(os.path.join(paper_dir, "pmc_fulltext.xml"), fulltext_artifacts.get("pmc_xml"))
        _write_bytes(os.path.join(paper_dir, "europepmc_fulltext.xml"), fulltext_artifacts.get("europepmc_xml"))
        _write_bytes(os.path.join(paper_dir, "preprint_fulltext.xml"), fulltext_artifacts.get("preprint_xml"))
        _write_bytes(os.path.join(paper_dir, "unpaywall_fulltext.pdf"), fulltext_artifacts.get("unpaywall_pdf"))
        _write_bytes(os.path.join(paper_dir, "s2_fulltext.pdf"), fulltext_artifacts.get("s2_pdf"))

        if not text:
            print("skipped (no content)", file=sys.stderr)
            stubs.append(_stub("no content retrievable", paper=paper))
            continue

        # ── Optional screening step ──────────────────────────────────────────
        if criterion:
            try:
                screening = screen_paper(client, paper, criterion, text)
            except Exception as e:
                print(f"screening error: {e}", file=sys.stderr)
                stubs.append(_stub(f"screening error: {e}", paper=paper))
                continue

            if not screening.get("relevant"):
                print(f"not relevant ({screening.get('reason', '')})", file=sys.stderr)
                continue

            print(f"relevant [{source}]", end="", file=sys.stderr)
        else:
            print(f"[{source}]", end="", file=sys.stderr)

        # ── Common citation/linked-PMID helpers ──────────────────────────────
        lookup_pmid = paper.get("pmid")
        if not lookup_pmid and id_type == "geo":
            linked = paper.get("pmids", [])
            lookup_pmid = linked[0] if linked else None

        # ── Optional extraction step ─────────────────────────────────────────
        if output_schema:
            print(" — extracting fields", file=sys.stderr)
            try:
                result = extract_fields(client, paper, output_schema, text, extraction_max_tokens)
                result["citation_count"] = get_citation_count(
                    doi=paper.get("doi"), pmid=lookup_pmid
                )
                if id_type == "geo":
                    result["linked_pmids"] = ", ".join(paper.get("pmids", []))
                results.append(result)
            except Exception as e:
                print(f"  extraction error: {e}", file=sys.stderr)
                stubs.append(_stub(f"extraction error: {e}", paper=paper))
        else:
            print("", file=sys.stderr)
            result = {
                "title": paper.get("title"),
                "authors": paper.get("authors"),
                "url": paper.get("url"),
                "pmid": paper.get("pmid"),
                "doi": paper.get("doi"),
                "geo_accession": paper.get("geo_accession"),
                "text_source": source,
                "citation_count": get_citation_count(
                    doi=paper.get("doi"), pmid=lookup_pmid
                ),
            }
            if id_type == "geo":
                result["linked_pmids"] = ", ".join(paper.get("pmids", []))
            results.append(result)

    if not results:
        print("\nNo records to write.", file=sys.stderr)
        if markdown and stubs:
            print("Generating markdown summary (stubs only)...", file=sys.stderr)
            md_path = csv_path.replace(".csv", ".md")
            md_content = generate_markdown_summary(client, stubs, output_schema, markdown_max_tokens)
            _write_text(md_path, md_content)
            print(f"Wrote markdown to {md_path}", file=sys.stderr)
        return None, 0

    priority = ["title", "authors", "url", "pmid", "doi", "geo_accession", "text_source", "citation_count"]
    all_keys = list(dict.fromkeys(k for r in results for k in r.keys()))
    fieldnames = priority + [k for k in all_keys if k not in priority]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    print(f"\nWrote {len(results)} records to {csv_path}", file=sys.stderr)

    if markdown:
        print("Generating markdown summary...", file=sys.stderr)
        md_path = csv_path.replace(".csv", ".md")
        md_content = generate_markdown_summary(client, results + stubs, output_schema, markdown_max_tokens)
        _write_text(md_path, md_content)
        print(f"Wrote markdown to {md_path}", file=sys.stderr)

    return csv_path, len(results)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value)[:120]


def _make_run_dir(base_output_path: str) -> tuple[str, str]:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = os.path.dirname(base_output_path) or "."
    csv_name = os.path.basename(base_output_path) or "results.csv"
    os.makedirs(base_dir, exist_ok=True)
    run_dir = os.path.join(base_dir, f"run_{ts}")
    os.makedirs(run_dir, exist_ok=True)
    return run_dir, os.path.join(run_dir, csv_name)


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text or "")


def _write_bytes(path: str, data: bytes | None) -> None:
    if data:
        with open(path, "wb") as f:
            f.write(data)

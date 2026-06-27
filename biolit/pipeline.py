"""Pipeline orchestration: fetch → full-text → parse → screen → extract → CSV."""
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime

from biolit.fetchers._hooks import FetchContext, run_registered_fetchers
from biolit.fetchers.geo import fetch_geo_record
from biolit.fetchers.pubmed import fetch_pubmed_metadata, fetch_pmc_fulltext, doi_to_pmid
from biolit.fetchers.europepmc import fetch_europepmc_fulltext
from biolit.fetchers.preprints import fetch_preprint, fetch_preprint_metadata
from biolit.fetchers.unpaywall import fetch_via_unpaywall
from biolit.fetchers.semantic_scholar import fetch_s2_pdf, get_citation_count
from biolit.fetchers.openalex import fetch_via_openalex
from biolit.fetchers.europepmc_pdf import fetch_europepmc_pdf
from biolit.fetchers.core import fetch_via_core
from biolit.fetchers.landing_page import fetch_via_landing_page
from biolit.fetchers.custom_resolvers import fetch_via_custom_resolvers
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


def _screen_prompt(paper: dict, criterion: str, text: str) -> str:
    return (
        f"You are screening a scientific paper for relevance to a literature review.\n\n"
        f"Criterion: {criterion}\n\n"
        f"Title: {paper['title']}\n"
        f"MeSH terms: {', '.join(paper.get('mesh_terms', []))}\n\n"
        f"Paper content:\n{text}\n\n"
        f"Respond with valid JSON only, no other text:\n"
        f'{{"relevant": true or false, "reason": "one sentence"}}'
    )


def screen_paper(client: BaseLLMClient, paper: dict, criterion: str, text: str) -> dict:
    """Ask the LLM whether *paper* meets *criterion* given *text* as evidence."""
    response = client.chat(
        [{"role": "user", "content": _screen_prompt(paper, criterion, text)}],
        max_tokens=256,
    )
    return parse_json_response(response)


def _extract_prompt(paper: dict, output_schema: dict, text: str) -> str:
    schema_str = json.dumps(output_schema, indent=2)
    return (
        f"Extract structured information from this paper.\n"
        f"Use only what is stated in the paper content — do not speculate.\n\n"
        f"Title: {paper['title']}\n"
        f"MeSH terms: {', '.join(paper.get('mesh_terms', []))}\n\n"
        f"Paper content:\n{text}\n\n"
        f"Extract the following fields. Respond with valid JSON only, no other text.\n"
        f"Fields to extract:\n{schema_str}\n\n"
        f"Return a JSON object with exactly those keys. Use null for fields not determinable."
    )


def _attach_paper_metadata(result: dict, paper: dict) -> dict:
    result["title"] = paper["title"]
    result["authors"] = paper.get("authors")
    result["url"] = paper.get("url")
    result["pmid"] = paper.get("pmid")
    result["doi"] = paper.get("doi")
    result["geo_accession"] = paper.get("geo_accession")
    result["text_source"] = paper.get("text_source", "abstract")
    return result


def extract_fields(client: BaseLLMClient, paper: dict, output_schema: dict, text: str, extraction_max_tokens: int = 4096) -> dict:
    """Extract structured fields from *paper* using the LLM."""
    response = client.chat(
        [{"role": "user", "content": _extract_prompt(paper, output_schema, text)}],
        max_tokens=extraction_max_tokens,
    )
    result = parse_json_response(response)
    return _attach_paper_metadata(result, paper)


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

_MARKDOWN_META_KEYS = frozenset({
    "title", "authors", "url", "pmid", "doi", "geo_accession", "text_source",
    "citation_count", "linked_pmids", "_stub", "_stub_reason",
})


def _markdown_header(record: dict) -> str:
    title = record.get("title") or "Unknown"
    url = record.get("url", "")
    pmid = record.get("pmid")
    doi = record.get("doi")
    geo = record.get("geo_accession")
    text_source = record.get("text_source", "")
    citations = record.get("citation_count")
    authors = record.get("authors")

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

    return f"## {title}\n" + "\n".join(meta_lines)


def _markdown_body_prompt(record: dict, output_schema: dict) -> str:
    extracted = {k: v for k, v in record.items() if k not in _MARKDOWN_META_KEYS}
    schema_str = json.dumps(output_schema, indent=2)
    extracted_str = json.dumps(extracted, indent=2)
    return (
        "You are writing a structured markdown summary section for an academic literature review.\n\n"
        f"Paper metadata:\n"
        f"  Title: {record.get('title') or 'Unknown'}\n"
        f"  PMID: {record.get('pmid')}\n"
        f"  DOI: {record.get('doi')}\n"
        f"  Text source: {record.get('text_source', '')}\n\n"
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


def _stub_markdown(record: dict) -> str:
    reason = record.get("_stub_reason", "unknown error")
    return f"{_markdown_header(record)}\n\n_Record could not be fully processed: {reason}._\n"


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
    if record.get("_stub"):
        return _stub_markdown(record)
    header = _markdown_header(record)
    if not output_schema:
        return f"{header}\n"
    body = client.chat(
        [{"role": "user", "content": _markdown_body_prompt(record, output_schema)}],
        max_tokens=markdown_max_tokens,
    )
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


def generate_markdown_summary_batch(
    client: BaseLLMClient,
    records: list[dict],
    output_schema: dict | None,
    markdown_max_tokens: int = 1024,
) -> str:
    """Like :func:`generate_markdown_summary` but uses a single batch LLM call.

    Stubs and metadata-only records skip the LLM (same as the sequential path).
    Records whose batch slot returns an empty response fall back to a stub
    rendering so the document remains complete.
    """
    needs_llm_idx = [
        i for i, r in enumerate(records)
        if not r.get("_stub") and output_schema
    ]
    bodies: list[str] = []
    if needs_llm_idx:
        prompts = [
            [{"role": "user", "content": _markdown_body_prompt(records[i], output_schema)}]
            for i in needs_llm_idx
        ]
        bodies = client.chat_batch(prompts, max_tokens=markdown_max_tokens)

    body_by_idx = dict(zip(needs_llm_idx, bodies))
    sections = ["# Literature Search Results\n"]
    for i, record in enumerate(records):
        if record.get("_stub"):
            sections.append(_stub_markdown(record))
            continue
        header = _markdown_header(record)
        if not output_schema:
            sections.append(f"{header}\n")
            continue
        body = body_by_idx.get(i, "")
        if not body:
            sections.append(f"{header}\n\n_Markdown rendering failed in batch._\n")
        else:
            sections.append(f"{header}\n\n{body}\n")
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

def _pdf_to_sections_text(
    pdf_bytes: bytes,
    sections_wanted: list[str] | None,
    max_tokens: int,
) -> str | None:
    """Parse *pdf_bytes* into section text, or None if empty / unparseable.

    Mirrors the inline Unpaywall / S2 PDF-parsing path so every PDF source
    returns text through the same ``parse_pdf_sections`` + ``select_sections``
    pipeline. A missing ``pdfminer.six`` is downgraded to a warning + None
    rather than an exception, matching the existing behaviour.
    """
    try:
        secs = parse_pdf_sections(pdf_bytes)
    except ImportError:
        print("  [warning] pdfminer.six not installed; skipping PDF parsing", file=sys.stderr)
        return None
    if secs:
        return select_sections(secs, sections_wanted, max_tokens)
    return None


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
      6. OpenAlex green-OA PDF
      7. Europe PMC open-access full-text PDF
      8. CORE aggregated green-OA PDF (opt-in; needs CORE_API_KEY)
      9. Publisher landing-page scrape (citation_pdf_url meta tag)
     10. Custom user-configured resolvers (institutional OpenURL / proxy;
         opt-in via BIOLIT_CUSTOM_RESOLVERS)
     11. Abstract only

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

    # 6. OpenAlex green-OA PDF — author manuscripts (often with full Methods)
    #    that Unpaywall / S2 miss. Key-less; polite pool via unpaywall_email.
    if doi:
        pdf_bytes = fetch_via_openalex(doi, mailto=unpaywall_email)
        if pdf_bytes:
            artifacts["openalex_pdf"] = pdf_bytes
            text = _pdf_to_sections_text(pdf_bytes, sections_wanted, max_tokens)
            if text:
                return text, "openalex_pdf", artifacts

    # 7. Europe PMC open-access full-text PDF (OA subset only)
    if pmid or doi:
        pdf_bytes = fetch_europepmc_pdf(pmid=pmid, doi=doi)
        if pdf_bytes:
            artifacts["europepmc_oa_pdf"] = pdf_bytes
            text = _pdf_to_sections_text(pdf_bytes, sections_wanted, max_tokens)
            if text:
                return text, "europepmc_oa_pdf", artifacts

    # 8. CORE aggregated green-OA PDF — opt-in (no-op unless CORE_API_KEY set)
    if doi:
        pdf_bytes = fetch_via_core(doi)
        if pdf_bytes:
            artifacts["core_pdf"] = pdf_bytes
            text = _pdf_to_sections_text(pdf_bytes, sections_wanted, max_tokens)
            if text:
                return text, "core_pdf", artifacts

    # 9. Publisher landing-page scrape — follow the DOI to the article page
    #    and read its advertised citation_pdf_url. Catches OA PDFs the
    #    aggregator APIs mislabel or never index. OA-only; preprints skipped.
    page_url = paper.get("url")
    if doi or page_url:
        pdf_bytes = fetch_via_landing_page(doi=doi, url=None if doi else page_url)
        if pdf_bytes:
            artifacts["landing_page_pdf"] = pdf_bytes
            text = _pdf_to_sections_text(pdf_bytes, sections_wanted, max_tokens)
            if text:
                return text, "landing_page_pdf", artifacts

    # 10. Custom user-configured resolvers (institutional OpenURL / library
    #     proxy). No-op unless BIOLIT_CUSTOM_RESOLVERS is configured.
    if doi or page_url:
        pdf_bytes = fetch_via_custom_resolvers(doi=doi, url=page_url)
        if pdf_bytes:
            artifacts["custom_resolver_pdf"] = pdf_bytes
            text = _pdf_to_sections_text(pdf_bytes, sections_wanted, max_tokens)
            if text:
                return text, "custom_resolver_pdf", artifacts

    # 11. Abstract fallback
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

def _make_stub(reason: str, paper: dict | None = None, id_str: str = "") -> dict:
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


def _persist_record_artifacts(
    paper: dict, text: str, source: str, fulltext_artifacts: dict,
    artifacts_root: str, id_str: str,
) -> None:
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
    _write_bytes(os.path.join(paper_dir, "openalex_fulltext.pdf"), fulltext_artifacts.get("openalex_pdf"))
    _write_bytes(os.path.join(paper_dir, "europepmc_oa_fulltext.pdf"), fulltext_artifacts.get("europepmc_oa_pdf"))
    _write_bytes(os.path.join(paper_dir, "core_fulltext.pdf"), fulltext_artifacts.get("core_pdf"))
    _write_bytes(os.path.join(paper_dir, "landing_page_fulltext.pdf"), fulltext_artifacts.get("landing_page_pdf"))
    _write_bytes(os.path.join(paper_dir, "custom_resolver_fulltext.pdf"), fulltext_artifacts.get("custom_resolver_pdf"))


def _lookup_pmid_for_citations(paper: dict, id_type: str) -> str | None:
    pmid = paper.get("pmid")
    if pmid:
        return pmid
    if id_type == "geo":
        linked = paper.get("pmids", [])
        return linked[0] if linked else None
    return None


def _build_metadata_only_result(paper: dict, source: str, id_type: str) -> dict:
    lookup_pmid = _lookup_pmid_for_citations(paper, id_type)
    result = {
        "title": paper.get("title"),
        "authors": paper.get("authors"),
        "url": paper.get("url"),
        "pmid": paper.get("pmid"),
        "doi": paper.get("doi"),
        "geo_accession": paper.get("geo_accession"),
        "text_source": source,
        "citation_count": get_citation_count(doi=paper.get("doi"), pmid=lookup_pmid),
    }
    if id_type == "geo":
        result["linked_pmids"] = ", ".join(paper.get("pmids", []))
    return result


def _fetch_and_resolve_all(
    ids: list[str],
    artifacts_root: str,
    unpaywall_email: str | None,
    sections_wanted: list[str] | None,
    max_tokens: int,
) -> tuple[list[dict], list[dict]]:
    """Fetch metadata, resolve full text, and persist artifacts for each id.

    Returns ``(prepared, stubs)`` where each item in *prepared* is a dict with
    keys ``id_str``, ``id_type``, ``paper``, ``text``, ``source`` ready for the
    LLM stages.  Failures (missing record / no content) go into *stubs*.
    """
    prepared: list[dict] = []
    stubs: list[dict] = []

    for i, id_str in enumerate(ids, 1):
        id_type = _detect_id_type(id_str)
        print(f"[{i}/{len(ids)}] {id_str}", end=" ... ", flush=True, file=sys.stderr)

        try:
            paper = fetch_record(id_str)
        except Exception as e:
            print(f"fetch error: {e}", file=sys.stderr)
            stubs.append(_make_stub(f"fetch error: {e}", id_str=id_str))
            continue

        if not paper:
            print("skipped (not found)", file=sys.stderr)
            stubs.append(_make_stub("record not found", id_str=id_str))
            continue

        print("resolving full text...", end=" ", flush=True, file=sys.stderr)
        if id_type == "geo":
            text, source, ftarts = _resolve_geo_fulltext(
                paper, unpaywall_email, sections_wanted, max_tokens
            )
        else:
            text, source, ftarts = resolve_fulltext(
                paper, unpaywall_email, sections_wanted, max_tokens
            )
        paper["text_source"] = source
        _persist_record_artifacts(paper, text, source, ftarts, artifacts_root, id_str)

        if not text:
            print("skipped (no content)", file=sys.stderr)
            stubs.append(_make_stub("no content retrievable", paper=paper))
            continue

        print(f"[{source}]", file=sys.stderr)
        prepared.append({
            "id_str": id_str, "id_type": id_type,
            "paper": paper, "text": text, "source": source,
        })

    return prepared, stubs


def _run_batch_loop(
    client: BaseLLMClient,
    ids: list[str],
    criterion: str | None,
    output_schema: dict | None,
    artifacts_root: str,
    unpaywall_email: str | None,
    sections_wanted: list[str] | None,
    max_tokens: int,
    extraction_max_tokens: int,
) -> tuple[list[dict], list[dict]]:
    """Batch variant: fetch all records first, then batch screen + extract."""
    prepared, stubs = _fetch_and_resolve_all(
        ids, artifacts_root, unpaywall_email, sections_wanted, max_tokens
    )

    if criterion and prepared:
        print(f"\nBatch screening {len(prepared)} records...", file=sys.stderr)
        prompts = [
            [{"role": "user", "content": _screen_prompt(p["paper"], criterion, p["text"])}]
            for p in prepared
        ]
        responses = client.chat_batch(prompts, max_tokens=256)
        kept = []
        for p, response in zip(prepared, responses):
            if not response:
                stubs.append(_make_stub("screening error: empty batch response", paper=p["paper"]))
                continue
            try:
                screening = parse_json_response(response)
            except Exception as e:
                stubs.append(_make_stub(f"screening error: {e}", paper=p["paper"]))
                continue
            if screening.get("relevant"):
                print(f"  ✓ {p['id_str']} relevant [{p['source']}]", file=sys.stderr)
                kept.append(p)
            else:
                print(f"  ✗ {p['id_str']} not relevant ({screening.get('reason', '')})", file=sys.stderr)
        prepared = kept

    results: list[dict] = []
    if not prepared:
        return results, stubs

    if output_schema:
        print(f"\nBatch extracting fields from {len(prepared)} records...", file=sys.stderr)
        prompts = [
            [{"role": "user", "content": _extract_prompt(p["paper"], output_schema, p["text"])}]
            for p in prepared
        ]
        responses = client.chat_batch(prompts, max_tokens=extraction_max_tokens)
        for p, response in zip(prepared, responses):
            paper, id_type = p["paper"], p["id_type"]
            if not response:
                stubs.append(_make_stub("extraction error: empty batch response", paper=paper))
                continue
            try:
                result = parse_json_response(response)
                _attach_paper_metadata(result, paper)
            except Exception as e:
                stubs.append(_make_stub(f"extraction error: {e}", paper=paper))
                continue
            lookup_pmid = _lookup_pmid_for_citations(paper, id_type)
            result["citation_count"] = get_citation_count(doi=paper.get("doi"), pmid=lookup_pmid)
            if id_type == "geo":
                result["linked_pmids"] = ", ".join(paper.get("pmids", []))
            results.append(result)
    else:
        for p in prepared:
            results.append(_build_metadata_only_result(p["paper"], p["source"], p["id_type"]))

    return results, stubs


def _run_sequential_loop(
    client: BaseLLMClient,
    ids: list[str],
    criterion: str | None,
    output_schema: dict | None,
    artifacts_root: str,
    unpaywall_email: str | None,
    sections_wanted: list[str] | None,
    max_tokens: int,
    extraction_max_tokens: int,
) -> tuple[list[dict], list[dict]]:
    """Sequential variant: per-record fetch → screen → extract, one at a time."""
    results: list[dict] = []
    stubs: list[dict] = []

    for i, id_str in enumerate(ids, 1):
        id_type = _detect_id_type(id_str)
        print(f"[{i}/{len(ids)}] {id_str}", end=" ... ", flush=True, file=sys.stderr)

        try:
            paper = fetch_record(id_str)
        except Exception as e:
            print(f"fetch error: {e}", file=sys.stderr)
            stubs.append(_make_stub(f"fetch error: {e}", id_str=id_str))
            continue

        if not paper:
            print("skipped (not found)", file=sys.stderr)
            stubs.append(_make_stub("record not found", id_str=id_str))
            continue

        print("resolving full text...", end=" ", flush=True, file=sys.stderr)
        if id_type == "geo":
            text, source, ftarts = _resolve_geo_fulltext(
                paper, unpaywall_email, sections_wanted, max_tokens
            )
        else:
            text, source, ftarts = resolve_fulltext(
                paper, unpaywall_email, sections_wanted, max_tokens
            )
        paper["text_source"] = source
        _persist_record_artifacts(paper, text, source, ftarts, artifacts_root, id_str)

        if not text:
            print("skipped (no content)", file=sys.stderr)
            stubs.append(_make_stub("no content retrievable", paper=paper))
            continue

        if criterion:
            try:
                screening = screen_paper(client, paper, criterion, text)
            except Exception as e:
                print(f"screening error: {e}", file=sys.stderr)
                stubs.append(_make_stub(f"screening error: {e}", paper=paper))
                continue
            if not screening.get("relevant"):
                print(f"not relevant ({screening.get('reason', '')})", file=sys.stderr)
                continue
            print(f"relevant [{source}]", end="", file=sys.stderr)
        else:
            print(f"[{source}]", end="", file=sys.stderr)

        if output_schema:
            print(" — extracting fields", file=sys.stderr)
            try:
                result = extract_fields(client, paper, output_schema, text, extraction_max_tokens)
                lookup_pmid = _lookup_pmid_for_citations(paper, id_type)
                result["citation_count"] = get_citation_count(doi=paper.get("doi"), pmid=lookup_pmid)
                if id_type == "geo":
                    result["linked_pmids"] = ", ".join(paper.get("pmids", []))
                results.append(result)
            except Exception as e:
                print(f"  extraction error: {e}", file=sys.stderr)
                stubs.append(_make_stub(f"extraction error: {e}", paper=paper))
        else:
            print("", file=sys.stderr)
            results.append(_build_metadata_only_result(paper, source, id_type))

    return results, stubs


# ---------------------------------------------------------------------------
# High-level paper fetch — resolve best-available full text from any of
# accession / DOI / PMID, with a bare-PMID last-resort fallback.
# ---------------------------------------------------------------------------

_FULLTEXT_SOURCES = frozenset({
    "pmc_fulltext", "europepmc_fulltext", "preprint_fulltext",
    "unpaywall_pdf", "s2_pdf", "openalex_pdf", "europepmc_oa_pdf",
    "core_pdf", "landing_page_pdf", "custom_resolver_pdf",
    "geo_linked_fulltext",
})


@dataclass
class PaperResult:
    """Outcome of :func:`fetch_paper`. ``is_fulltext`` is True when ``source``
    is a real full-text source rather than a bare abstract / metadata record."""

    text: str
    source: str
    is_fulltext: bool
    pmid: str | None = None
    doi: str | None = None
    title: str | None = None


def _resolve_one(
    record_id: str | None,
    *,
    fallback_pmid: str | None = None,
    unpaywall_email: str | None = None,
    sections_wanted: list[str] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> PaperResult:
    """Resolve a single id (accession / DOI / PMID) to a :class:`PaperResult`."""
    if not record_id:
        return PaperResult("", "no_id", False, fallback_pmid, None, None)
    paper = fetch_record(record_id)
    if not paper or paper.get("error"):
        return PaperResult("", "no_record", False, fallback_pmid, None, None)
    resolver = _resolve_geo_fulltext if paper.get("geo_accession") else resolve_fulltext
    try:
        text, source, _artifacts = resolver(
            paper,
            unpaywall_email=unpaywall_email,
            sections_wanted=sections_wanted,
            max_tokens=max_tokens,
        )
    except Exception as e:  # noqa: BLE001 — a fetch failure is not fatal
        return PaperResult(
            "", f"resolve_error:{type(e).__name__}", False,
            paper.get("pmid") or fallback_pmid, paper.get("doi"), paper.get("title"),
        )
    return PaperResult(
        text or "", source, source in _FULLTEXT_SOURCES,
        (str(paper.get("pmid")) if paper.get("pmid") else fallback_pmid),
        paper.get("doi"), paper.get("title"),
    )


def fetch_paper(
    accession: str | None = None,
    pmid: str | None = None,
    doi: str | None = None,
    *,
    unpaywall_email: str | None = None,
    sections_wanted: list[str] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> PaperResult:
    """Fetch best-available full text given any of accession / PMID / DOI.

    Resolution order — stop at the first that yields real full text:

      1. **Accession** (e.g. a GEO GSE) — walks the record's linked PMIDs.
      2. **DOI** — the preprint path: a caller's finder may resolve a
         bioRxiv / medRxiv DOI with no PMID, and the preprint fetcher pulls
         JATS for ``10.1101/`` etc.
      3. **Bare PMID** — last-resort fallback. A GEO record often has NO
         linked publication (the submitter never linked it), so the
         accession path yields only a metadata record — but a caller (e.g. a
         title / author search) may have resolved the real PMID. Fetch the
         paper for that PMID directly.

    Returns the best :class:`PaperResult` across the attempted ids: full text
    if any path found it, else the longest non-empty text, else the primary.
    """
    primary = _resolve_one(
        (accession or pmid or "").strip() or None,
        fallback_pmid=pmid, unpaywall_email=unpaywall_email,
        sections_wanted=sections_wanted, max_tokens=max_tokens,
    )
    if primary.is_fulltext:
        return primary
    best = primary
    if doi and doi.strip():
        secondary = _resolve_one(
            doi.strip(), fallback_pmid=pmid, unpaywall_email=unpaywall_email,
            sections_wanted=sections_wanted, max_tokens=max_tokens,
        )
        if secondary.is_fulltext:
            return secondary
        if secondary.text and not best.text:
            best = secondary
    if pmid and pmid.strip() and (accession or doi):
        tertiary = _resolve_one(
            pmid.strip(), fallback_pmid=pmid, unpaywall_email=unpaywall_email,
            sections_wanted=sections_wanted, max_tokens=max_tokens,
        )
        if tertiary.is_fulltext or (tertiary.text and not best.text):
            best = tertiary
    return best


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
    batch: bool = False,
) -> tuple[str | None, int]:
    """Fetch, optionally screen, and optionally extract a mixed list of PMIDs, DOIs, and GEO accessions.

    Each identifier is auto-detected and routed to the appropriate fetcher.
    All record types flow through the same fetch → (screen) → (extract) → artifacts → CSV loop.

    - If *criterion* is None, the screening step is skipped and all records proceed to extraction.
    - If *fields_description* is None, the extraction step is skipped and only metadata columns
      (title, url, pmid, doi, geo_accession, text_source, citation_count) are written.
    - The output CSV always includes pmid, doi, and geo_accession columns.
    - When *batch* is True, screening, extraction, and markdown rendering each
      use a single batch LLM call (Anthropic Message Batches / OpenAI Batch API,
      ~50% cheaper). Fetch + full-text resolution remain sequential.
    """
    output_schema: dict | None = None
    if fields_description:
        print("Building output schema...", file=sys.stderr)
        output_schema = build_output_schema(client, fields_description)
        print(f"  Fields: {', '.join(output_schema.keys())}\n", file=sys.stderr)

    print(f"Processing {len(ids)} identifiers{' (batch mode)' if batch else ''}\n", file=sys.stderr)

    if batch and len(ids) == 1:
        print(
            "  [warning] batch mode with a single record pays the full batch-API "
            "queue overhead (typically several minutes per stage) for ~50% cost "
            "savings on one call. Consider running without --batch.\n",
            file=sys.stderr,
        )

    run_dir, csv_path = _make_run_dir(output_path)
    artifacts_root = os.path.join(run_dir, "artifacts")
    os.makedirs(artifacts_root, exist_ok=True)
    print(f"Run directory: {run_dir}\n", file=sys.stderr)

    loop = _run_batch_loop if batch else _run_sequential_loop
    results, stubs = loop(
        client, ids, criterion, output_schema, artifacts_root,
        unpaywall_email, sections_wanted, max_tokens, extraction_max_tokens,
    )

    md_renderer = generate_markdown_summary_batch if batch else generate_markdown_summary

    if not results:
        print("\nNo records to write.", file=sys.stderr)
        if markdown and stubs:
            print("Generating markdown summary (stubs only)...", file=sys.stderr)
            md_path = csv_path.replace(".csv", ".md")
            md_content = md_renderer(client, stubs, output_schema, markdown_max_tokens)
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
        md_content = md_renderer(client, results + stubs, output_schema, markdown_max_tokens)
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

"""MCP server for biolit.

Exposes biolit's fetching, screening, and extraction capabilities as MCP tools
so any MCP-compatible client (Claude Desktop, OpenAI Agents SDK, etc.) can call
them directly and orchestrate them as part of larger workflows.

Usage:
    biolit-mcp                                  # run as an MCP server (stdio transport)
    biolit-mcp --provider openai                # override LLM_PROVIDER env var
    biolit-mcp --provider openai --model gpt-4o-mini

CLI flags (override the env vars below):
    --provider          Provider to use (anthropic | openai | ollama)
    --model             Model name (uses provider default if unset)

Environment variables (same as the CLI):
    ANTHROPIC_API_KEY   Required for Anthropic provider (default)
    OPENAI_API_KEY      Required for OpenAI provider
    LLM_PROVIDER        Provider to use (anthropic | openai | ollama)
    LLM_MODEL           Model name (uses provider default if unset)
    NCBI_API_KEY        Optional; raises NCBI rate limit from 3/s to 10/s
    UNPAYWALL_EMAIL     Used by fetch_fulltext if not passed as an argument
"""
import argparse
import os
from importlib.metadata import version as _pkg_version, PackageNotFoundError

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from biolit.config import load_config

from biolit.fetchers.geo import fetch_geo_record as _fetch_geo_record
from biolit.fetchers.pubmed import fetch_pubmed_metadata as _fetch_pubmed_metadata, doi_to_pmid, doi_to_pmcid
from biolit.fetchers.semantic_scholar import get_s2_pdf_url
from biolit.llm import get_llm_client
from biolit.parsers.utils import DEFAULT_MAX_TOKENS
from biolit.pipeline import (
    build_output_schema,
    extract_fields as _extract_fields,
    resolve_fulltext,
    _resolve_geo_fulltext,
    run as _run,
    screen_paper as _screen_paper,
)
from biolit.utils import extract_pmids, read_eml_body, read_ids_file, read_dois_from_bib

load_dotenv(override=True)

mcp = FastMCP("biolit")

# Provider/model resolution: CLI flags (set in main()) override env vars.
# The LLM client is lazily constructed on first LLM-touching tool call so that
# the server still starts (and non-LLM tools still work) when the configured
# provider's key isn't available.
_provider = os.environ.get("LLM_PROVIDER", "anthropic")
_model = os.environ.get("LLM_MODEL")
_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_llm_client(_provider, _model)
    return _llm


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_version() -> str:
    """Return the installed biolit package version."""
    try:
        return _pkg_version("biolit")
    except PackageNotFoundError:
        return "unknown"


@mcp.tool()
def fetch_pubmed_metadata(pmid: str) -> dict:
    """Fetch metadata for a PubMed ID.

    Returns title, abstract, MeSH terms, DOI, and a PubMed URL.
    Use this before calling screen_paper or extract_fields so you have the
    paper text and metadata to pass along.
    """
    result = _fetch_pubmed_metadata(pmid)
    if result is None:
        return {"error": f"No record found for PMID {pmid}"}
    return result


@mcp.tool()
def fetch_geo_record(accession: str) -> dict:
    """Fetch and parse a GEO series record.

    Accepts GSE, GDS, GSM, or GPL accessions. Returns title, summary,
    overall design, experiment type, organism(s), and any linked PMIDs.
    The 'abstract' key contains a combined text blob suitable for passing
    to screen_paper or extract_fields.
    """
    result = _fetch_geo_record(accession)
    if result is None:
        return {"error": f"No record found for accession {accession}"}
    return result


@mcp.tool()
def fetch_fulltext(
    pmid: str,
    unpaywall_email: str = "",
    sections: str = "",
) -> dict:
    """Retrieve full text for a PubMed ID.

    Tries sources in order: PMC JATS XML → Europe PMC JATS XML →
    preprint XML (bioRxiv/medRxiv) → Unpaywall PDF →
    Semantic Scholar open-access PDF → abstract fallback.

    Args:
        pmid: PubMed ID.
        unpaywall_email: Your email for the Unpaywall API. Falls back to the
            UNPAYWALL_EMAIL env var. Required only for the Unpaywall step.
        sections: Comma-separated section names to include, e.g.
            "methods,results". Default: all sections.

    Returns:
        {"text": "...", "source": "pmc_fulltext" | "europepmc_fulltext" |
                                   "preprint_fulltext" | "unpaywall_pdf" |
                                   "s2_pdf" | "abstract"}
    """
    paper = _fetch_pubmed_metadata(pmid)
    if paper is None:
        return {"error": f"No record found for PMID {pmid}"}

    sections_wanted = [s.strip() for s in sections.split(",") if s.strip()] if sections else None
    email = unpaywall_email or os.environ.get("UNPAYWALL_EMAIL")

    text, source, _ = resolve_fulltext(paper, unpaywall_email=email, sections_wanted=sections_wanted)
    return {"text": text, "source": source}


@mcp.tool()
def fetch_geo_fulltext(
    accession: str,
    unpaywall_email: str = "",
    sections: str = "",
) -> dict:
    """Retrieve full text for a GEO accession via its linked PubMed IDs.

    Fetches the GEO record, then tries each linked PMID using the same
    full-text chain as fetch_fulltext (PMC → Europe PMC → Unpaywall → S2).
    Falls back to the linked paper's abstract, then to the GEO metadata text.

    Args:
        accession: GEO accession, e.g. "GSE53987".
        unpaywall_email: Your email for the Unpaywall API. Falls back to the
            UNPAYWALL_EMAIL env var.
        sections: Comma-separated section names to include, e.g.
            "methods,results". Default: all sections.

    Returns:
        {"text": "...", "source": "geo_linked_fulltext" | "geo_linked_abstract" | "geo_record"}
    """
    record = _fetch_geo_record(accession)
    if record is None:
        return {"error": f"No record found for accession {accession}"}

    sections_wanted = [s.strip() for s in sections.split(",") if s.strip()] if sections else None
    email = unpaywall_email or os.environ.get("UNPAYWALL_EMAIL")

    text, source, _ = _resolve_geo_fulltext(record, unpaywall_email=email, sections_wanted=sections_wanted)
    return {"text": text, "source": source}


@mcp.tool()
def screen_paper(
    title: str,
    text: str,
    criterion: str,
    mesh_terms: str = "",
) -> dict:
    """Ask the LLM whether a paper meets a relevance criterion.

    Args:
        title: Paper title.
        text: Abstract or full text to screen.
        criterion: A yes/no question about relevance, e.g.
            "Is this paper specifically about schizophrenia genomics?"
        mesh_terms: Comma-separated MeSH terms (optional but improves accuracy).

    Returns:
        {"relevant": true | false, "reason": "one sentence"}
    """
    paper = {
        "title": title,
        "mesh_terms": [t.strip() for t in mesh_terms.split(",") if t.strip()],
    }
    return _screen_paper(_get_llm(), paper, criterion, text)


@mcp.tool()
def extract_fields(
    title: str,
    text: str,
    fields: str,
    mesh_terms: str = "",
) -> dict:
    """Extract structured fields from a paper using the LLM.

    Args:
        title: Paper title.
        text: Abstract or full text to extract from.
        fields: Comma-separated field names, e.g.
            "methodology, sample_type, causal_claims, summary"
        mesh_terms: Comma-separated MeSH terms (optional).

    Returns:
        JSON object with one key per requested field, plus title, url,
        pmid, doi, and text_source keys.
    """
    paper = {
        "title": title,
        "mesh_terms": [t.strip() for t in mesh_terms.split(",") if t.strip()],
        # These are unknown at this level; the caller can post-process if needed.
        "pmid": None,
        "doi": None,
        "url": None,
        "text_source": "mcp",
    }
    llm = _get_llm()
    schema = build_output_schema(llm, fields)
    return _extract_fields(llm, paper, schema, text)


@mcp.tool()
def resolve_doi(doi: str) -> dict:
    """Resolve a DOI to a PMID and/or PMCID via the NCBI ID Converter.

    Useful for chaining with other tools when you have a DOI but need a PMID.

    Args:
        doi: A DOI string, e.g. "10.1038/s41588-021-00974-7".

    Returns:
        {"doi": "...", "pmid": "..." | null, "pmcid": "..." | null}
    """
    return {
        "doi": doi,
        "pmid": doi_to_pmid(doi),
        "pmcid": doi_to_pmcid(doi),
    }


@mcp.tool()
def lookup_s2_pdf(doi: str) -> dict:
    """Look up whether Semantic Scholar has an open-access PDF for a DOI.

    Useful for checking PDF availability before committing to a full fetch,
    or for retrieving the PDF URL to pass to another tool.

    Args:
        doi: DOI string, e.g. "10.1101/2021.11.01.466731".

    Returns:
        {"doi": "...", "pdf_url": "..." | null,
         "available": true | false}
    """
    url = get_s2_pdf_url(doi)
    return {"doi": doi, "pdf_url": url, "available": url is not None}


@mcp.tool()
def run_pipeline(
    ids: str = "",
    criterion: str = "",
    fields: str = "",
    output_path: str = "",
    unpaywall_email: str = "",
    config_path: str = "",
    markdown: bool = False,
    markdown_max_tokens: int = 0,
    extraction_max_tokens: int = 0,
    max_tokens: int = 0,
    bib_path: str = "",
    ids_file: str = "",
) -> dict:
    """Run the fetch → (screen) → (extract) pipeline on a mixed list of identifiers and write a CSV.

    Accepts PMIDs, DOIs, and GEO accessions in any combination. Each identifier
    is auto-detected and routed to the appropriate fetcher. Full-text retrieval
    is attempted for all record types — GEO records use linked PMIDs as the
    source, falling back to GEO metadata if none are accessible.

    This is equivalent to running `biolit --ids ...` from the command line.

    Args:
        ids: Comma-separated identifiers — PMIDs, DOIs, GEO accessions, or any mix.
            Example: "41795042,GSE53987,10.1101/2025.03.17.25324098"
            Leave empty to use ids from bib_path, ids_file, or config_path.
        criterion: Relevance screening question. Leave empty to skip screening and
            process all records. Overrides config_path value.
        fields: Comma-separated field names to extract, e.g.
            "methodology, sample_type, causal_claims, summary".
            Leave empty to use the default fields or the value from config_path.
            Overrides config_path value.
        output_path: Path for the output CSV. Overrides config_path value.
            A timestamped run directory is created alongside it.
        unpaywall_email: Email for the Unpaywall API.
            Falls back to config_path value, then UNPAYWALL_EMAIL env var.
        config_path: Path to a JSON config file. Supported keys: ids, criterion, fields,
            output, unpaywall_email, markdown, markdown_max_tokens, extraction_max_tokens, max_tokens. Explicit arguments take precedence.
        markdown: If True, also write a results.md markdown summary alongside the CSV.
        markdown_max_tokens: Maximum tokens for each markdown section LLM call (default: 1024).
            Pass 0 to use the default.
        extraction_max_tokens: Maximum tokens for each field extraction LLM call (default: 4096).
            Pass 0 to use the default.
        max_tokens: Maximum tokens of paper text sent to the LLM (default: 12500).
            Pass 0 to use the default.
        bib_path: Path to a BibTeX (.bib) file. DOIs are extracted from doi={...} fields.
            Used when ids is empty. Takes precedence over ids_file.
        ids_file: Path to a plain-text file of identifiers (one per line, mixed types OK).
            Used when ids and bib_path are both empty.

    Returns:
        {"output_path": "..." | null, "relevant_count": N}
    """
    # Load config file if provided; explicit args override it
    config: dict = {}
    if config_path:
        try:
            config = load_config(config_path)
        except Exception as e:
            return {"error": f"Failed to load config: {e}"}

    from biolit.cli import DEFAULT_FIELDS

    # Resolve identifiers: inline ids > bib_path > ids_file > config ids
    if ids:
        id_list = [x.strip() for x in ids.split(",") if x.strip()]
    elif bib_path:
        try:
            id_list = read_dois_from_bib(bib_path)
        except Exception as e:
            return {"error": f"Failed to read bib file: {e}"}
    elif ids_file:
        try:
            id_list = read_ids_file(ids_file)
        except Exception as e:
            return {"error": f"Failed to read ids file: {e}"}
    else:
        resolved_ids = config.get("ids") or ""
        id_list = [x.strip() for x in resolved_ids.split(",") if x.strip()]

    if not id_list:
        return {"error": "No identifiers provided. Pass 'ids', 'bib_path', 'ids_file', or set 'ids' in config_path."}

    resolved_criterion = criterion or config.get("criterion") or None
    resolved_fields = fields or config.get("fields") or DEFAULT_FIELDS
    resolved_output = output_path or config.get("output") or "results.csv"
    resolved_email = unpaywall_email or config.get("unpaywall_email") or os.environ.get("UNPAYWALL_EMAIL")
    resolved_markdown = markdown or bool(config.get("markdown"))
    resolved_markdown_max_tokens = markdown_max_tokens or config.get("markdown_max_tokens") or 1024
    resolved_extraction_max_tokens = extraction_max_tokens or config.get("extraction_max_tokens") or 4096
    resolved_max_tokens = max_tokens or config.get("max_tokens") or DEFAULT_MAX_TOKENS
    csv_path, relevant_count = _run(
        client=_get_llm(),
        ids=id_list,
        criterion=resolved_criterion,
        fields_description=resolved_fields,
        output_path=resolved_output,
        unpaywall_email=resolved_email,
        markdown=resolved_markdown,
        markdown_max_tokens=resolved_markdown_max_tokens,
        extraction_max_tokens=resolved_extraction_max_tokens,
        max_tokens=resolved_max_tokens,
    )
    return {"output_path": csv_path, "relevant_count": relevant_count}


@mcp.tool()
def read_pmids_from_eml(eml_path: str) -> dict:
    """Parse PMIDs out of a PubMed alert .eml file.

    Args:
        eml_path: Absolute path to the .eml file on disk.

    Returns:
        {"pmids": ["12345678", ...], "count": N}
    """
    body = read_eml_body(eml_path)
    pmids = extract_pmids(body)
    return {"pmids": pmids, "count": len(pmids)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global _provider, _model
    parser = argparse.ArgumentParser(prog="biolit-mcp", description="biolit MCP server (stdio transport).")
    parser.add_argument("--provider", help="LLM provider (anthropic|openai|ollama). Overrides LLM_PROVIDER.")
    parser.add_argument("--model", help="Model name. Overrides LLM_MODEL.")
    args = parser.parse_args()
    if args.provider:
        _provider = args.provider
    if args.model:
        _model = args.model
    mcp.run()


if __name__ == "__main__":
    main()

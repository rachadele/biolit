"""MCP server for biolit.

Exposes biolit's fetching, screening, and extraction capabilities as MCP tools
so any MCP-compatible client (Claude Desktop, OpenAI Agents SDK, etc.) can call
them directly and orchestrate them as part of larger workflows.

Usage:
    biolit-mcp          # run as an MCP server (stdio transport)

Environment variables (same as the CLI):
    ANTHROPIC_API_KEY   Required for Anthropic provider (default)
    OPENAI_API_KEY      Required for OpenAI provider
    LLM_PROVIDER        Provider to use (anthropic | openai | ollama)
    LLM_MODEL           Model name (uses provider default if unset)
    NCBI_API_KEY        Optional; raises NCBI rate limit from 3/s to 10/s
    UNPAYWALL_EMAIL     Used by fetch_fulltext if not passed as an argument
"""
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from biolit.fetchers.geo import fetch_geo_record as _fetch_geo_record
from biolit.fetchers.pubmed import fetch_pubmed_metadata
from biolit.llm import get_llm_client
from biolit.pipeline import (
    build_output_schema,
    extract_fields as _extract_fields,
    resolve_fulltext,
    screen_paper as _screen_paper,
    screen_by_pmid as _screen_by_pmid,
    screen_by_geo as _screen_by_geo,
)
from biolit.utils import extract_pmids, read_eml_body

load_dotenv()

mcp = FastMCP("biolit")

# Initialise one LLM client for the lifetime of the server process.
# Override with LLM_PROVIDER / LLM_MODEL env vars.
_provider = os.environ.get("LLM_PROVIDER", "anthropic")
_model = os.environ.get("LLM_MODEL")
_llm = get_llm_client(_provider, _model)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def search_pubmed(pmid: str) -> dict:
    """Fetch metadata for a PubMed ID.

    Returns title, abstract, MeSH terms, DOI, and a PubMed URL.
    Use this before calling screen_paper or extract_fields so you have the
    paper text and metadata to pass along.
    """
    result = fetch_pubmed_metadata(pmid)
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

    Tries sources in order: PMC JATS XML → preprint XML (bioRxiv/medRxiv)
    → Unpaywall PDF → abstract fallback.

    Args:
        pmid: PubMed ID.
        unpaywall_email: Your email for the Unpaywall API. Falls back to the
            UNPAYWALL_EMAIL env var. Required only for the Unpaywall step.
        sections: Comma-separated section names to include, e.g.
            "methods,results". Default: all sections.

    Returns:
        {"text": "...", "source": "pmc_fulltext" | "preprint_fulltext" |
                                   "unpaywall_pdf" | "abstract"}
    """
    paper = fetch_pubmed_metadata(pmid)
    if paper is None:
        return {"error": f"No record found for PMID {pmid}"}

    sections_wanted = [s.strip() for s in sections.split(",") if s.strip()] if sections else None
    email = unpaywall_email or os.environ.get("UNPAYWALL_EMAIL")

    text, source, _ = resolve_fulltext(paper, unpaywall_email=email, sections_wanted=sections_wanted)
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
    return _screen_paper(_llm, paper, criterion, text)


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
    schema = build_output_schema(_llm, fields)
    return _extract_fields(_llm, paper, schema, text)


@mcp.tool()
def screen_by_pmid(
    pmid: str,
    criterion: str,
    fulltext: bool = False,
    unpaywall_email: str = "",
) -> dict:
    """Fetch a PubMed paper and screen it for relevance in one step.

    Args:
        pmid: PubMed ID.
        criterion: A yes/no relevance question, e.g.
            "Is this paper specifically about schizophrenia genomics?"
        fulltext: If True, attempt to retrieve full text before screening.
            Falls back to abstract if full text is unavailable.
        unpaywall_email: Your email for the Unpaywall API (only used when
            fulltext=True). Falls back to UNPAYWALL_EMAIL env var.

    Returns:
        {"relevant": true | false, "reason": "one sentence",
         "text_source": "abstract" | "pmc_fulltext" | ...}
    """
    email = unpaywall_email or os.environ.get("UNPAYWALL_EMAIL")
    return _screen_by_pmid(_llm, pmid, criterion, fulltext=fulltext, unpaywall_email=email)


@mcp.tool()
def screen_by_geo(
    accession: str,
    criterion: str,
) -> dict:
    """Fetch a GEO record and screen it for relevance in one step.

    Args:
        accession: GEO accession (e.g. GSE123456).
        criterion: A yes/no relevance question, e.g.
            "Is this dataset about schizophrenia?"

    Returns:
        {"relevant": true | false, "reason": "one sentence",
         "text_source": "geo_metadata"}
    """
    return _screen_by_geo(_llm, accession, criterion)


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
    mcp.run()


if __name__ == "__main__":
    main()

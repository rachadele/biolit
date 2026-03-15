"""Pipeline orchestration: fetch → full-text → parse → screen → extract → CSV."""
import csv
import json
import os
from datetime import datetime

from biolit.fetchers.geo import fetch_geo_record
from biolit.fetchers.pubmed import fetch_pubmed_metadata, fetch_pmc_fulltext
from biolit.fetchers.preprints import fetch_preprint
from biolit.fetchers.unpaywall import fetch_via_unpaywall
from biolit.llm.base import BaseLLMClient
from biolit.parsers.jats import parse_jats_sections
from biolit.parsers.pdf import parse_pdf_sections
from biolit.parsers.utils import select_sections, DEFAULT_MAX_CHARS
from biolit.utils import parse_json_response


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def build_output_schema(client: BaseLLMClient, fields_description: str) -> dict:
    """Translate a comma-separated field list into a schema dict via the LLM."""
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
        max_tokens=150,
    )
    return parse_json_response(response)


def extract_fields(client: BaseLLMClient, paper: dict, output_schema: dict, text: str) -> dict:
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
        max_tokens=500,
    )
    result = parse_json_response(response)
    result["title"] = paper["title"]
    result["url"] = paper["url"]
    result["pmid"] = paper["pmid"]
    result["doi"] = paper.get("doi")
    result["text_source"] = paper.get("text_source", "abstract")
    return result


# ---------------------------------------------------------------------------
# Full-text resolution
# ---------------------------------------------------------------------------

def resolve_fulltext(
    paper: dict,
    unpaywall_email: str | None = None,
    sections_wanted: list[str] | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> tuple[str, str, dict]:
    """Attempt to fetch full text for *paper*, returning (text, source_label).

    Fallback chain:
      1. PMC JATS XML
      2. Preprint JATS XML (bioRxiv / medRxiv)
      3. Unpaywall PDF
      4. Abstract only

    *sections_wanted* filters which sections are concatenated (None = all).
    Returns a (text, source) tuple.
    """
    artifacts: dict = {}
    pmid = paper["pmid"]
    doi = paper.get("doi")

    # 1. PMC
    xml_bytes = fetch_pmc_fulltext(pmid)
    if xml_bytes:
        artifacts["pmc_xml"] = xml_bytes
        secs = parse_jats_sections(xml_bytes)
        if secs:
            return select_sections(secs, sections_wanted, max_chars), "pmc_fulltext", artifacts

    # 2. Preprints
    if doi:
        xml_bytes = fetch_preprint(doi)
        if xml_bytes:
            artifacts["preprint_xml"] = xml_bytes
            secs = parse_jats_sections(xml_bytes)
            if secs:
                return select_sections(secs, sections_wanted, max_chars), "preprint_fulltext", artifacts

    # 3. Unpaywall PDF
    if doi and unpaywall_email:
        pdf_bytes = fetch_via_unpaywall(doi, unpaywall_email)
        if pdf_bytes:
            artifacts["unpaywall_pdf"] = pdf_bytes
            try:
                secs = parse_pdf_sections(pdf_bytes)
                if secs:
                    return select_sections(secs, sections_wanted, max_chars), "unpaywall_pdf", artifacts
            except ImportError:
                print("  [warning] pdfminer.six not installed; skipping PDF parsing")

    # 4. Abstract fallback
    return paper.get("abstract", ""), "abstract", artifacts


# ---------------------------------------------------------------------------
# Single-record convenience functions
# ---------------------------------------------------------------------------

def screen_by_pmid(
    client: BaseLLMClient,
    pmid: str,
    criterion: str,
    fulltext: bool = False,
    unpaywall_email: str | None = None,
) -> dict:
    """Fetch a PubMed paper and screen it for relevance in one call.

    Returns the screening result dict plus a ``text_source`` key.
    """
    paper = fetch_pubmed_metadata(pmid)
    if paper is None:
        return {"error": f"No record found for PMID {pmid}"}

    if fulltext:
        text, source, _ = resolve_fulltext(paper, unpaywall_email=unpaywall_email)
    else:
        text = paper.get("abstract", "")
        source = "abstract"

    result = screen_paper(client, paper, criterion, text)
    result["text_source"] = source
    return result


def screen_by_geo(
    client: BaseLLMClient,
    accession: str,
    criterion: str,
) -> dict:
    """Fetch a GEO record and screen it for relevance in one call.

    Returns the screening result dict plus a ``text_source`` key.
    """
    record = fetch_geo_record(accession)
    if record is None:
        return {"error": f"No record found for accession {accession}"}

    text = record.get("abstract", "")
    paper = {"title": record.get("title", accession), "mesh_terms": []}
    result = screen_paper(client, paper, criterion, text)
    result["text_source"] = "geo_metadata"
    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(
    client: BaseLLMClient,
    pmids: list[str],
    criterion: str,
    fields_description: str,
    output_path: str,
    fulltext: bool = False,
    unpaywall_email: str | None = None,
    sections_wanted: list[str] | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> None:
    # Step 1: translate field names into a schema
    print("Building output schema...")
    output_schema = build_output_schema(client, fields_description)
    print(f"  Fields: {', '.join(output_schema.keys())}\n")

    print(f"Processing {len(pmids)} PMIDs\n")

    run_dir, csv_path = _make_run_dir(output_path)
    artifacts_root = os.path.join(run_dir, "artifacts")
    os.makedirs(artifacts_root, exist_ok=True)
    print(f"Run directory: {run_dir}\n")

    # Step 3: fetch → (optionally) full text → screen → extract
    results = []
    for i, pmid in enumerate(pmids, 1):
        print(f"[{i}/{len(pmids)}] PMID {pmid}", end=" ... ", flush=True)

        try:
            paper = fetch_pubmed_metadata(pmid)
        except Exception as e:
            print(f"fetch error: {e}")
            continue

        if not paper:
            print("skipped (not found)")
            continue

        if fulltext:
            print("resolving full text...", end=" ", flush=True)
            text, source, fulltext_artifacts = resolve_fulltext(
                paper, unpaywall_email, sections_wanted, max_chars
            )
            paper["text_source"] = source
        else:
            text = paper.get("abstract", "")
            paper["text_source"] = "abstract"
            fulltext_artifacts = {}

        # Persist what we retrieved/sent so the run is reproducible and inspectable.
        paper_slug = f"{pmid}_{_safe_name(paper.get('title', 'paper'))}"
        paper_dir = os.path.join(artifacts_root, paper_slug)
        os.makedirs(paper_dir, exist_ok=True)
        _write_text(os.path.join(paper_dir, "selected_text.txt"), text)
        _write_text(
            os.path.join(paper_dir, "metadata.json"),
            json.dumps(
                {
                    "pmid": paper.get("pmid"),
                    "doi": paper.get("doi"),
                    "title": paper.get("title"),
                    "url": paper.get("url"),
                    "mesh_terms": paper.get("mesh_terms", []),
                    "text_source": paper.get("text_source"),
                },
                indent=2,
            ),
        )
        _write_bytes(os.path.join(paper_dir, "pmc_fulltext.xml"), fulltext_artifacts.get("pmc_xml"))
        _write_bytes(os.path.join(paper_dir, "preprint_fulltext.xml"), fulltext_artifacts.get("preprint_xml"))
        _write_bytes(os.path.join(paper_dir, "unpaywall_fulltext.pdf"), fulltext_artifacts.get("unpaywall_pdf"))

        if not text:
            print("skipped (no content)")
            continue

        try:
            screening = screen_paper(client, paper, criterion, text)
        except Exception as e:
            print(f"screening error: {e}")
            continue

        if not screening.get("relevant"):
            print(f"not relevant ({screening.get('reason', '')})")
            continue

        source_label = paper.get("text_source", "abstract")
        print(f"relevant [{source_label}] — extracting fields")

        try:
            result = extract_fields(client, paper, output_schema, text)
            results.append(result)
        except Exception as e:
            print(f"  extraction error: {e}")

    # Step 4: write CSV
    if not results:
        print("\nNo relevant papers found.")
        return

    priority = ["title", "url", "pmid", "doi", "text_source"]
    all_keys = list(dict.fromkeys(k for r in results for k in r.keys()))
    fieldnames = priority + [k for k in all_keys if k not in priority]

    # Keep compatibility: --output now determines parent folder + CSV filename.
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    print(f"\nWrote {len(results)} relevant papers to {csv_path}")


def run_geo(
    client: BaseLLMClient,
    accessions: list[str],
    criterion: str,
    fields_description: str,
    output_path: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> None:
    """Run the screening + extraction pipeline on a list of GEO accessions.

    Each GEO record's summary and overall-design text is used in place of an
    abstract. Full-text fetching is not applicable — the record text IS the
    content. The output CSV has the same structure as the PubMed pipeline.
    """
    # Step 1: translate field names into a schema
    print("Building output schema...")
    output_schema = build_output_schema(client, fields_description)
    print(f"  Fields: {', '.join(output_schema.keys())}\n")

    print(f"Processing {len(accessions)} GEO accessions\n")

    run_dir, csv_path = _make_run_dir(output_path)
    artifacts_root = os.path.join(run_dir, "artifacts")
    os.makedirs(artifacts_root, exist_ok=True)
    print(f"Run directory: {run_dir}\n")

    results = []
    for i, accession in enumerate(accessions, 1):
        print(f"[{i}/{len(accessions)}] {accession}", end=" ... ", flush=True)

        try:
            paper = fetch_geo_record(accession)
        except Exception as e:
            print(f"fetch error: {e}")
            continue

        if not paper:
            print("skipped (not found)")
            continue

        text = paper.get("abstract", "")
        if len(text) > max_chars:
            text = text[:max_chars]

        paper_slug = f"{accession}_{_safe_name(paper.get('title', 'record'))}"
        paper_dir = os.path.join(artifacts_root, paper_slug)
        os.makedirs(paper_dir, exist_ok=True)
        _write_text(os.path.join(paper_dir, "selected_text.txt"), text)
        _write_text(
            os.path.join(paper_dir, "metadata.json"),
            json.dumps(
                {
                    "accession": accession,
                    "title": paper.get("title"),
                    "url": paper.get("url"),
                    "pmids": paper.get("pmids", []),
                    "text_source": paper.get("text_source"),
                },
                indent=2,
            ),
        )

        if not text:
            print("skipped (no content)")
            continue

        try:
            screening = screen_paper(client, paper, criterion, text)
        except Exception as e:
            print(f"screening error: {e}")
            continue

        if not screening.get("relevant"):
            print(f"not relevant ({screening.get('reason', '')})")
            continue

        print("relevant — extracting fields")

        try:
            result = extract_fields(client, paper, output_schema, text)
            result.pop("pmid", None)  # accession is the identifier for GEO records
            result["geo_accession"] = accession
            result["pmids"] = ", ".join(paper.get("pmids", []))
            results.append(result)
        except Exception as e:
            print(f"  extraction error: {e}")

    if not results:
        print("\nNo relevant records found.")
        return

    priority = ["title", "url", "geo_accession", "pmids", "doi", "text_source"]
    all_keys = list(dict.fromkeys(k for r in results for k in r.keys()))
    fieldnames = priority + [k for k in all_keys if k not in priority]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    print(f"\nWrote {len(results)} relevant records to {csv_path}")


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value)[:120]


def _make_run_dir(base_output_path: str) -> tuple[str, str]:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = os.path.dirname(base_output_path) or "."
    csv_name = os.path.basename(base_output_path) or "results.csv"
    # Ensure parent exists, then create a timestamped run folder.
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


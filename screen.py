#!/usr/bin/env python3
"""
PubMed Alert Literature Screener — Agentic Version

Like screen.py but with a configurable screening criterion and output fields.
A single Claude call translates the user's field descriptions into a structured
schema, then a Python loop handles fetch/screen/extract for each paper.

Usage:
    python agent.py pubmed.eml
    python agent.py pubmed.eml --default
    python agent.py pubmed.eml --criterion "Is this about schizophrenia genomics?" --fields "methodology, sample_type, summary"
    python agent.py pubmed.eml --output results.csv
"""

import os
import re
import csv
import sys
import json
import email
import time
import argparse
import xml.etree.ElementTree as ET

import requests
import anthropic
from dotenv import load_dotenv

load_dotenv()

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
MODEL = "claude-haiku-4-5-20251001"

DEFAULT_CRITERION = (
    "Is this paper SPECIFICALLY about schizophrenia AND does it use genetics "
    "or genomics methods (e.g. GWAS, WGS, scRNA-seq, proteomics, gene expression)?"
)
DEFAULT_FIELDS = "methodology, sample_type, causal_claims, genetics_claims, summary"


# --- Helpers (shared with screen.py) ---

def read_eml_body(eml_path):
    """Extract body text from a PubMed alert .eml file."""
    with open(eml_path, "rb") as f:
        msg = email.message_from_binary_file(f)
    body = ""
    for part in msg.walk():
        if part.get_content_type() in ("text/plain", "text/html"):
            payload = part.get_payload(decode=True)
            if payload:
                body += payload.decode("utf-8", errors="ignore")
    return body


def extract_pmids(body):
    """Extract unique PMIDs from email body text."""
    pmids = re.findall(r'PMID:\s*(\d+)', body)
    seen = set()
    unique = []
    for p in pmids:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def fetch_pubmed_data(pmid):
    """Fetch title, abstract, and MeSH terms for a PMID via NCBI E-utilities."""
    response = requests.get(
        f"{NCBI_BASE}efetch.fcgi",
        params={"db": "pubmed", "id": pmid, "rettype": "xml", "retmode": "xml"},
        timeout=15
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
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
    return {
        "pmid": pmid,
        "title": title,
        "abstract": abstract,
        "mesh_terms": mesh_terms,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


def parse_json_response(text):
    """Parse JSON from Claude's response, stripping markdown code fences if present."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return json.loads(text.strip())


# --- Claude calls ---

def build_output_schema(client, fields_description):
    """Use Claude to turn a comma-separated field list into a schema dict.

    The schema dict maps field names to plain-English descriptions of what
    to extract, which is then passed verbatim into the extraction prompt.
    """
    prompt = f"""Convert this list of field names into a JSON object where each key is a field name and each value is a clear description of what to extract from a scientific paper abstract.

Fields: {fields_description}

Respond with valid JSON only, no other text. Example format:
{{"methodology": "general experimental method used (e.g. GWAS, WGS, scRNA-seq)", "sample_type": "tissue type and biological origin of samples"}}"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    return parse_json_response(response.content[0].text)


def screen_paper(client, paper, criterion):
    """Ask Claude whether a paper meets the relevance criterion."""
    prompt = f"""You are screening a scientific paper for relevance to a literature review.

Criterion: {criterion}

Title: {paper['title']}
Abstract: {paper['abstract']}
MeSH terms: {', '.join(paper['mesh_terms'])}

Respond with valid JSON only, no other text:
{{"relevant": true or false, "reason": "one sentence"}}"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}]
    )
    return parse_json_response(response.content[0].text)


def extract_fields(client, paper, output_schema):
    """Extract structured fields from a paper using the provided schema."""
    schema_str = json.dumps(output_schema, indent=2)
    prompt = f"""Extract structured information from this paper.
Use only what is stated in the abstract — do not speculate.

Title: {paper['title']}
Abstract: {paper['abstract']}
MeSH terms: {', '.join(paper['mesh_terms'])}

Extract the following fields. Respond with valid JSON only, no other text.
Fields to extract:
{schema_str}

Return a JSON object with exactly those keys. Use null for fields not determinable."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    result = parse_json_response(response.content[0].text)
    result["title"] = paper["title"]
    result["url"] = paper["url"]
    result["pmid"] = paper["pmid"]
    return result


# --- Pipeline ---

def run(client, eml_path, criterion, fields_description, output_path):
    # Step 1: translate field names into a schema
    print("Building output schema...")
    output_schema = build_output_schema(client, fields_description)
    print(f"  Fields: {', '.join(output_schema.keys())}\n")

    # Step 2: parse email
    body = read_eml_body(eml_path)
    pmids = extract_pmids(body)
    print(f"Found {len(pmids)} papers in {eml_path}\n")

    # Step 3: fetch → screen → extract
    results = []
    for i, pmid in enumerate(pmids, 1):
        print(f"[{i}/{len(pmids)}] PMID {pmid}", end=" ... ")

        try:
            paper = fetch_pubmed_data(pmid)
            time.sleep(0.4)  # NCBI rate limit
        except Exception as e:
            print(f"fetch error: {e}")
            continue

        if not paper or not paper.get("abstract"):
            print("skipped (no abstract)")
            continue

        try:
            screening = screen_paper(client, paper, criterion)
        except Exception as e:
            print(f"screening error: {e}")
            continue

        if not screening.get("relevant"):
            print(f"not relevant ({screening.get('reason', '')})")
            continue

        print(f"relevant — extracting fields")

        try:
            result = extract_fields(client, paper, output_schema)
            results.append(result)
        except Exception as e:
            print(f"  extraction error: {e}")

    # Step 4: write CSV
    if not results:
        print("\nNo relevant papers found.")
        return

    priority = ["title", "url", "pmid"]
    all_keys = list(dict.fromkeys(k for r in results for k in r.keys()))
    fieldnames = priority + [k for k in all_keys if k not in priority]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    print(f"\nWrote {len(results)} relevant papers to {output_path}")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="Screen PubMed alert emails with a configurable criterion and output fields."
    )
    parser.add_argument("eml_file", help="Path to the PubMed alert .eml file")
    parser.add_argument("--criterion", default=None,
                        help="Relevance screening criterion (yes/no question)")
    parser.add_argument("--fields", default=None,
                        help="Fields to extract (comma-separated names)")
    parser.add_argument("--default", action="store_true",
                        help="Use default schizophrenia genomics criterion and fields")
    parser.add_argument("--output", default="results.csv",
                        help="Output CSV path (default: results.csv)")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: set ANTHROPIC_API_KEY in your environment or a .env file")
        sys.exit(1)

    if args.default:
        criterion = DEFAULT_CRITERION
        fields = DEFAULT_FIELDS
    else:
        criterion = args.criterion
        if not criterion:
            criterion = input("Screening criterion (yes/no question about relevance): ").strip()
        fields = args.fields
        if not fields:
            fields = input("Fields to extract (comma-separated, e.g. methodology, sample_type, summary): ").strip()

    client = anthropic.Anthropic(api_key=api_key)
    run(client, args.eml_file, criterion, fields, args.output)


if __name__ == "__main__":
    main()

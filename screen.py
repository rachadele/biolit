#!/usr/bin/env python3
"""
PubMed Alert Literature Screener

Reads a PubMed alert .eml file, screens each paper for relevance to
schizophrenia genomics using Claude, and extracts structured fields
from relevant papers into a CSV.

Usage:
    python screen.py pubmed.eml
    python screen.py pubmed.eml --output results.csv
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
OUTPUT_FIELDS = ["title", "url", "pmid", "methodology", "sample_type",
                 "causal_claims", "genetics_claims", "summary"]


# --- Email parsing ---

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


# --- NCBI ---

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


# --- Claude ---

def parse_json_response(text):
    """Parse JSON from Claude's response, stripping markdown code fences if present."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return json.loads(text.strip())


def screen_paper(client, paper):
    """Ask Claude (Haiku) whether this paper is relevant to schizophrenia genomics.

    Returns a dict with keys: relevant (bool), reason (str).
    """
    prompt = f"""You are screening a scientific paper for relevance to a literature review.

Criterion: Is this paper SPECIFICALLY about schizophrenia AND does it use genetics or genomics methods (e.g. GWAS, WGS, scRNA-seq, proteomics, gene expression)?

Title: {paper['title']}
Abstract: {paper['abstract']}
MeSH terms: {', '.join(paper['mesh_terms'])}

Respond with valid JSON only, no other text:
{{"relevant": true or false, "reason": "one sentence"}}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text
    try:
        return parse_json_response(raw)
    except json.JSONDecodeError:
        print(f"  [debug] screening raw response: {raw!r}")
        raise


def extract_fields(client, paper):
    """Ask Claude (Haiku) to extract structured fields from the abstract.

    Returns a dict with methodology, sample_type, causal_claims,
    genetics_claims, and summary. Fields may be null if not determinable.
    """
    prompt = f"""Extract structured information from this schizophrenia genomics paper.
Use only what is stated in the abstract — do not speculate.

Title: {paper['title']}
Abstract: {paper['abstract']}
MeSH terms: {', '.join(paper['mesh_terms'])}

Respond with valid JSON only, no other text:
{{
  "methodology": "general method (e.g. GWAS, WGS, scRNA-seq, proteomics, MRI) or null",
  "sample_type": "tissue/sample type and origin or null",
  "causal_claims": "any statements about causes of schizophrenia inferred from their data, or null",
  "genetics_claims": "any claims about specific genes, loci, or pathways, or null",
  "summary": "2-3 sentence plain-language summary for triage"
}}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text
    try:
        return parse_json_response(raw)
    except json.JSONDecodeError:
        print(f"  [debug] extraction raw response: {raw!r}")
        raise


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Screen PubMed alert emails for relevant papers.")
    parser.add_argument("eml_file", help="Path to the PubMed alert .eml file")
    parser.add_argument("--output", default="results.csv", help="Output CSV path (default: results.csv)")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: set ANTHROPIC_API_KEY in your environment or a .env file")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # Step 1: Parse email
    print(f"Reading {args.eml_file}...")
    body = read_eml_body(args.eml_file)
    pmids = extract_pmids(body)
    if not pmids:
        print("No PMIDs found in email.")
        sys.exit(1)
    print(f"Found {len(pmids)} papers: {', '.join(pmids)}\n")

    results = []

    for i, pmid in enumerate(pmids, 1):
        print(f"[{i}/{len(pmids)}] PMID {pmid}")

        # Step 2: Fetch from NCBI (rate limit: ~3 req/sec without API key)
        try:
            paper = fetch_pubmed_data(pmid)
            time.sleep(0.4)
        except Exception as e:
            print(f"  Failed to fetch: {e}")
            continue

        if not paper or not paper['abstract']:
            print(f"  No abstract available, skipping")
            continue

        # Step 3: Screen for relevance
        try:
            screening = screen_paper(client, paper)
        except Exception as e:
            print(f"  Screening failed ({type(e).__name__}): {e}")
            continue

        relevant = screening.get('relevant', False)
        reason = screening.get('reason', '')
        print(f"  {'RELEVANT' if relevant else 'skip'} — {reason}")

        if not relevant:
            continue

        # Step 4: Extract structured fields
        try:
            fields = extract_fields(client, paper)
        except Exception as e:
            print(f"  Extraction failed: {e}")
            fields = {k: None for k in ["methodology", "sample_type",
                                         "causal_claims", "genetics_claims", "summary"]}

        results.append({
            "title": paper['title'],
            "url": paper['url'],
            "pmid": pmid,
            **fields,
        })

    # Step 5: Write CSV
    print(f"\n{len(results)} relevant paper(s) found.")
    if results:
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(results)
        print(f"Results written to {args.output}")


if __name__ == "__main__":
    main()

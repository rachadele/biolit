#!/usr/bin/env python3
"""
PubMed Alert Literature Screener — Agentic Version

Uses a Claude agent (tool-use loop) to screen a PubMed alert .eml file
and extract structured fields from relevant papers. The screening criterion
and output fields are specified by the user at runtime.

Usage:
    python agent.py pubmed.eml
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
AGENT_MODEL = "claude-sonnet-4-6"       # orchestrates the tool-use loop
EXTRACTION_MODEL = "claude-haiku-4-5-20251001"  # screen/extract sub-calls (cheaper)


# --- Helper functions (reused from screen.py) ---

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


# --- Tool definitions ---

TOOLS = [
    {
        "name": "parse_email",
        "description": (
            "Parse a PubMed alert .eml file and return a list of PMIDs found in it. "
            "Call this first to discover which papers to process."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "eml_path": {
                    "type": "string",
                    "description": "Path to the .eml file."
                }
            },
            "required": ["eml_path"]
        }
    },
    {
        "name": "fetch_paper",
        "description": (
            "Fetch title, abstract, and MeSH terms for a single PMID from the NCBI "
            "E-utilities API. Returns an error key if the paper has no abstract."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pmid": {
                    "type": "string",
                    "description": "The PubMed ID (numeric string) to fetch."
                }
            },
            "required": ["pmid"]
        }
    },
    {
        "name": "screen_paper",
        "description": (
            "Screen a paper for relevance using Claude and the user-specified criterion. "
            "Returns {relevant: bool, reason: str}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pmid":       {"type": "string"},
                "title":      {"type": "string"},
                "abstract":   {"type": "string"},
                "mesh_terms": {"type": "array", "items": {"type": "string"}},
                "criterion":  {
                    "type": "string",
                    "description": "The relevance criterion as a yes/no question."
                }
            },
            "required": ["pmid", "title", "abstract", "mesh_terms", "criterion"]
        }
    },
    {
        "name": "extract_fields",
        "description": (
            "Extract structured fields from a paper using Claude. "
            "Pass an output_schema dict where keys are field names and values describe what to extract. "
            "Returns a dict matching the schema, plus title, url, and pmid."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pmid":          {"type": "string"},
                "title":         {"type": "string"},
                "abstract":      {"type": "string"},
                "mesh_terms":    {"type": "array", "items": {"type": "string"}},
                "url":           {"type": "string"},
                "output_schema": {
                    "type": "object",
                    "description": (
                        "Keys are field names, values are descriptions of what to extract. "
                        "Example: {\"methodology\": \"general method used\", "
                        "\"sample_type\": \"tissue and origin\"}"
                    )
                }
            },
            "required": ["pmid", "title", "abstract", "mesh_terms", "url", "output_schema"]
        }
    },
    {
        "name": "write_results",
        "description": (
            "Write the collected extraction results to a CSV file. "
            "Call this once, after all papers have been processed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "description": "List of result dicts, one per relevant paper.",
                    "items": {"type": "object"}
                },
                "output_path": {
                    "type": "string",
                    "description": "Path to the output CSV file."
                }
            },
            "required": ["results", "output_path"]
        }
    }
]


# --- Tool dispatcher ---

def dispatch_tool(tool_name, tool_input, client):
    """Execute a tool call and return the result as a dict."""

    if tool_name == "parse_email":
        body = read_eml_body(tool_input["eml_path"])
        pmids = extract_pmids(body)
        return {"pmids": pmids, "count": len(pmids)}

    elif tool_name == "fetch_paper":
        try:
            paper = fetch_pubmed_data(tool_input["pmid"])
            time.sleep(0.4)  # NCBI rate limit: ~3 req/sec without API key
        except Exception as e:
            return {"error": str(e)}
        if not paper or not paper.get("abstract"):
            return {"error": "No abstract available"}
        return paper

    elif tool_name == "screen_paper":
        prompt = f"""You are screening a scientific paper for relevance to a literature review.

Criterion: {tool_input['criterion']}

Title: {tool_input['title']}
Abstract: {tool_input['abstract']}
MeSH terms: {', '.join(tool_input['mesh_terms'])}

Respond with valid JSON only, no other text:
{{"relevant": true or false, "reason": "one sentence"}}"""

        response = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text
        try:
            return parse_json_response(raw)
        except json.JSONDecodeError:
            return {"error": f"Failed to parse screening response: {raw!r}"}

    elif tool_name == "extract_fields":
        schema_str = json.dumps(tool_input["output_schema"], indent=2)
        prompt = f"""Extract structured information from this paper.
Use only what is stated in the abstract — do not speculate.

Title: {tool_input['title']}
Abstract: {tool_input['abstract']}
MeSH terms: {', '.join(tool_input['mesh_terms'])}

Extract the following fields. Respond with valid JSON only, no other text.
Fields to extract:
{schema_str}

Return a JSON object with exactly those keys. Use null for fields not determinable."""

        response = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text
        try:
            result = parse_json_response(raw)
            # Always include these navigation fields in the output
            result["title"] = tool_input["title"]
            result["url"] = tool_input["url"]
            result["pmid"] = tool_input["pmid"]
            return result
        except json.JSONDecodeError:
            return {"error": f"Failed to parse extraction response: {raw!r}"}

    elif tool_name == "write_results":
        results = tool_input["results"]
        output_path = tool_input["output_path"]
        if not results:
            return {"written": 0, "path": output_path}
        # Derive column order: title/url/pmid first, then the rest
        priority = ["title", "url", "pmid"]
        all_keys = list(dict.fromkeys(k for r in results for k in r.keys()))
        fieldnames = priority + [k for k in all_keys if k not in priority]
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(results)
        return {"written": len(results), "path": output_path}

    else:
        return {"error": f"Unknown tool: {tool_name}"}


# --- Agent loop ---

def run_agent(client, eml_path, criterion, fields_description, output_path):
    """Run the Claude agent to screen and extract papers from a PubMed alert email."""

    system_prompt = f"""You are a literature screening agent. Your job is to process a PubMed alert email and produce a structured CSV of relevant papers.

The user's screening criterion:
  "{criterion}"

The fields to extract from each relevant paper:
  {fields_description}

Your plan:
1. Call parse_email to get the list of PMIDs.
2. For each PMID: call fetch_paper, then screen_paper using the criterion above.
3. For papers where screen_paper returns relevant=true: call extract_fields.
   Build an output_schema dict from the fields description above — keys are field names, values describe what to extract.
4. Once all papers are processed, call write_results with all collected results and output path "{output_path}".

Process papers one at a time (NCBI has rate limits). Print brief progress between papers."""

    messages = [{"role": "user", "content": f"Please process the PubMed alert email at: {eml_path}"}]

    while True:
        response = client.messages.create(
            model=AGENT_MODEL,
            max_tokens=4096,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        # Append full assistant turn to conversation history
        messages.append({"role": "assistant", "content": response.content})

        # Print any text blocks for live progress feedback
        for block in response.content:
            if block.type == "text":
                print(block.text)

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "max_tokens":
            print("\n[Warning] Hit max_tokens limit.")
            break

        if response.stop_reason != "tool_use":
            print(f"\n[Warning] Unexpected stop reason: {response.stop_reason}")
            break

        # Execute all tool calls in this turn and collect results
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            # Print a compact summary of the call (skip long fields like abstract)
            skip = {"abstract", "mesh_terms"}
            args_str = ", ".join(
                f"{k}={repr(v)[:50]}" for k, v in block.input.items() if k not in skip
            )
            print(f"  → {block.name}({args_str})")

            result = dispatch_tool(block.name, block.input, client)

            if "error" in result:
                print(f"    [error] {result['error']}")

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        messages.append({"role": "user", "content": tool_results})


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="Screen PubMed alert emails using a Claude agent."
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
        criterion = (
            "Is this paper SPECIFICALLY about schizophrenia AND does it use genetics "
            "or genomics methods (e.g. GWAS, WGS, scRNA-seq, proteomics, gene expression)?"
        )
        fields = "methodology, sample_type, causal_claims, genetics_claims, summary"
    else:
        criterion = args.criterion
        if not criterion:
            criterion = input("Screening criterion (yes/no question about relevance): ").strip()

        fields = args.fields
        if not fields:
            fields = input("Fields to extract (comma-separated, e.g. methodology, sample_type, summary): ").strip()

    client = anthropic.Anthropic(api_key=api_key)
    run_agent(client, args.eml_file, criterion, fields, args.output)


if __name__ == "__main__":
    main()

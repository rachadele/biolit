"""Shared helper utilities (email parsing, JSON cleaning, etc.)."""
import email
import json
import re


def read_eml_body(eml_path: str) -> str:
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


def extract_pmids(body: str) -> list[str]:
    """Extract unique PMIDs from email body text or HTML.

    Handles three formats emitted by NCBI PubMed alert emails:
    - Plain text:  ``PMID: 12345678``
    - HTML span:   ``<span class="docsum-pmid">12345678</span>``
    - HTML attr:   ``data-item-url="/12345678/"``
    """
    patterns = [
        r'PMID:\s*(\d+)',                        # plain text
        r'docsum-pmid[^>]*>(\d+)<',              # HTML span
        r'data-item-url=["\'/]+(\d+)["\'/]',     # HTML data attribute
    ]
    seen: set[str] = set()
    unique: list[str] = []
    for pat in patterns:
        for p in re.findall(pat, body):
            if p not in seen:
                seen.add(p)
                unique.append(p)
    return unique


def read_geo_file(path: str) -> list[str]:
    """Read a plain-text file of GEO accessions (one per line, comments ignored)."""
    accessions = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                accessions.append(line)
    return accessions


def read_pmids_file(path: str) -> list[str]:
    """Read a plain-text file of PMIDs (one per line, comments and blanks ignored)."""
    pmids = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and line.isdigit():
                pmids.append(line)
    return pmids


def parse_json_response(text: str) -> dict:
    """Parse JSON from an LLM response, stripping markdown code fences if present."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return json.loads(text.strip())



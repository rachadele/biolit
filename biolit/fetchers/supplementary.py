"""Fetch and extract text from a paper's supplementary materials.

The main-article fetchers (``pubmed`` / ``europepmc`` / ``preprints``)
return only the article body. Supplementary methods — where strain
backgrounds, cell-line provenance, antibody/reagent details, and other
curation-relevant facts often live — sit in *separate files* referenced
by ``<supplementary-material>`` elements in the JATS XML and bundled in
the paper's PubMed Central Open Access (OA) package. The article XML
carries only the supplement's caption, never its content, so a
full-text fetch alone never surfaces supplementary methods.

This module downloads the PMC OA ``.tar.gz`` package, restricts to the
files the JATS declares as supplementary material, and extracts their
text (PDF via pdfminer.six, DOCX via python-docx, plain text decoded
directly).

**Open-access only.** The PMC OA service serves the OA subset; for
paywalled articles the package is not available and this returns ``[]``.
"""
from __future__ import annotations

import io
import os
import re
import tarfile
import time
from dataclasses import dataclass

import requests

from .pubmed import doi_to_pmcid, pmid_to_pmcid

OA_SERVICE = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
_RATE_DELAY = 0.4  # be polite to the OA service

# Extensions we can turn into text, mapped to a coarse ``kind``.
_KIND_BY_EXT = {
    "pdf": "pdf",
    "docx": "docx",
    "txt": "text",
    "csv": "text",
    "tsv": "text",
}


@dataclass
class SuppFile:
    """One supplementary file from a paper's OA package.

    ``label`` is the caption/title the JATS attached to the file (e.g.
    "Supplementary Methods", "Table S1") — use it to pick the methods
    supplement out of a pile of data tables. ``text`` is the extracted
    body ("" when the format isn't text-extractable or extraction was
    skipped). ``kind`` is one of pdf / docx / text / other.
    """

    name: str
    kind: str
    label: str = ""
    text: str = ""
    n_bytes: int = 0


def _resolve_pmcid(
    pmcid: str | None, pmid: str | None, doi: str | None
) -> str | None:
    if pmcid:
        return pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
    if pmid:
        return pmid_to_pmcid(str(pmid))
    if doi:
        return doi_to_pmcid(doi)
    return None


def _oa_package_href(pmcid: str) -> str | None:
    """Ask the PMC OA service for the article's ``.tar.gz`` location.

    Returns the advertised href (an ``ftp://`` URL) or None when the
    article isn't in the OA subset / the service errors."""
    try:
        resp = requests.get(OA_SERVICE, params={"id": pmcid}, timeout=20)
        resp.raise_for_status()
    except Exception:
        return None
    m = re.search(r'<link[^>]*format="tgz"[^>]*href="([^"]+)"', resp.text)
    return m.group(1) if m else None


def _download_package(href: str) -> bytes | None:
    """Download the OA package gzip. The OA service advertises an
    ``ftp://`` URL; the same path is mirrored over HTTPS. Try HTTPS
    first (works through proxies/firewalls that block FTP), then fall
    back to FTP. Returns the gzip bytes, or None."""
    candidates = []
    if href.startswith("ftp://"):
        candidates.append(re.sub(r"^ftp://", "https://", href))
        candidates.append(href)
    else:
        candidates.append(href)
    for url in candidates:
        try:
            if url.startswith("ftp://"):
                import urllib.request

                with urllib.request.urlopen(url, timeout=120) as r:
                    data = r.read()
            else:
                resp = requests.get(url, timeout=120)
                resp.raise_for_status()
                data = resp.content
        except Exception:
            continue
        if data[:2] == b"\x1f\x8b":  # gzip magic
            return data
    return None


def _supp_labels_from_nxml(nxml: bytes) -> dict[str, str]:
    """Map ``filename-stem`` → caption for every JATS
    ``<supplementary-material>``. The stem (basename without extension,
    lowercased) is the join key because JATS hrefs occasionally drop the
    extension the packaged file carries (``media-1`` ↔ ``media-1.pdf``).
    """
    labels: dict[str, str] = {}
    try:
        from lxml import etree

        root = etree.fromstring(nxml)
    except Exception:
        return labels
    xlink = "http://www.w3.org/1999/xlink"
    for sm in root.iter("{*}supplementary-material"):
        href = sm.get(f"{{{xlink}}}href") or sm.get("href")
        if not href:
            # Some place the href on a child <media>.
            media = sm.find(".//{*}media")
            if media is not None:
                href = media.get(f"{{{xlink}}}href") or media.get("href")
        if not href:
            continue
        caption = " ".join(t.strip() for t in sm.itertext() if t and t.strip())
        stem = os.path.splitext(os.path.basename(href))[0].lower()
        labels[stem] = caption[:300]
    return labels


def _extract_text(name: str, data: bytes) -> tuple[str, str]:
    """Return ``(kind, text)`` for a supplementary file."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    kind = _KIND_BY_EXT.get(ext, "other")
    if kind == "pdf":
        from ..parsers.pdf import extract_pdf_text

        return kind, extract_pdf_text(data)
    if kind == "docx":
        from ..parsers.docx import extract_docx_text

        return kind, extract_docx_text(data)
    if kind == "text":
        return kind, data.decode("utf-8", "replace")
    return kind, ""


def fetch_supplementary(
    *,
    pmcid: str | None = None,
    pmid: str | None = None,
    doi: str | None = None,
    extract: bool = True,
) -> list[SuppFile]:
    """Fetch a paper's supplementary files from its PMC OA package.

    Provide exactly one identifier (``pmcid`` preferred; ``pmid`` / ``doi``
    are resolved to a PMCID via the NCBI ID converter). Returns the files
    the JATS declares as ``<supplementary-material>`` — the main-article
    PDF and inline figures bundled in the package are excluded. With
    ``extract=True`` (default) PDF / DOCX / text bodies are parsed into
    :attr:`SuppFile.text`; pass ``extract=False`` to list files only.

    Returns ``[]`` when the article isn't open access, has no declared
    supplement, or the package can't be retrieved. **Open access only** —
    paywalled supplements aren't reachable through this route.
    """
    resolved = _resolve_pmcid(pmcid, pmid, doi)
    if not resolved:
        return []
    href = _oa_package_href(resolved)
    if not href:
        return []
    blob = _download_package(href)
    time.sleep(_RATE_DELAY)
    if not blob:
        return []
    try:
        tar = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
        members = [m for m in tar.getmembers() if m.isfile()]
    except Exception:
        return []

    # The JATS in the package declares which files are supplementary.
    labels: dict[str, str] = {}
    for m in members:
        if m.name.lower().endswith((".nxml", ".xml")):
            f = tar.extractfile(m)
            if f:
                labels = _supp_labels_from_nxml(f.read())
            break
    if not labels:
        return []  # nothing declared as supplementary material

    out: list[SuppFile] = []
    for m in members:
        base = os.path.basename(m.name)
        stem = os.path.splitext(base)[0].lower()
        if stem not in labels:
            continue  # not a declared supplementary file (main PDF, figures, NXML)
        if extract:
            f = tar.extractfile(m)
            data = f.read() if f else b""
            kind, text = _extract_text(base, data)
        else:
            ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
            kind, text = _KIND_BY_EXT.get(ext, "other"), ""
        out.append(
            SuppFile(
                name=base,
                kind=kind,
                label=labels.get(stem, ""),
                text=text,
                n_bytes=m.size,
            )
        )
    return out

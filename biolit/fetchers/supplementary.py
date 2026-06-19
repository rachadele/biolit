"""Fetch and extract text from a paper's supplementary materials.

The main-article fetchers (``pubmed`` / ``europepmc`` / ``preprints``)
return only the article body. Supplementary methods — where strain
backgrounds, cell-line provenance, antibody/reagent details, and other
curation-relevant facts often live — sit in *separate files* referenced
by ``<supplementary-material>`` elements in the JATS XML. The article
XML carries only the supplement's caption, never its content, so a
full-text fetch alone never surfaces supplementary methods.

This module retrieves those files from **Europe PMC**:

* the ``supplementaryFiles`` endpoint returns a ZIP of the article's
  supplementary files (PDF / DOCX / tables / figures);
* the ``fullTextXML`` endpoint supplies the JATS, whose
  ``<supplementary-material>`` captions label each file (so callers can
  pick "Supplementary Methods" out of a pile of data tables).

It then extracts text (PDF via pdfminer.six, DOCX via python-docx, plain
text decoded directly).

**Open-access only.** Europe PMC serves supplementary files for the OA
subset; for paywalled articles the ZIP is unavailable and this returns
``[]``.

(The NCBI PMC OA ``oa_package`` FTP tree — the obvious-looking route —
is deprecated and no longer serves per-article packages, which is why
this goes through Europe PMC instead.)
"""
from __future__ import annotations

import io
import os
import time
import zipfile
from dataclasses import dataclass

import requests

from .pubmed import doi_to_pmcid, pmid_to_pmcid

EPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
_RATE_DELAY = 0.4  # be polite to Europe PMC

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
    """One supplementary file from a paper.

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


def _download_supp_zip(pmcid: str) -> bytes | None:
    """Download the Europe PMC supplementary-files ZIP for *pmcid*.

    Returns the ZIP bytes, or None when the article has no OA
    supplementary files / the request errors."""
    try:
        resp = requests.get(
            f"{EPMC_BASE}/{pmcid}/supplementaryFiles", timeout=120
        )
        resp.raise_for_status()
    except Exception:
        return None
    data = resp.content
    return data if data[:2] == b"PK" else None  # zip magic


def _supp_labels(pmcid: str) -> dict[str, str]:
    """Fetch the article JATS from Europe PMC and map each declared
    supplementary file's stem → caption. Empty dict on any failure
    (the caller then falls back to filename-based handling)."""
    try:
        resp = requests.get(f"{EPMC_BASE}/{pmcid}/fullTextXML", timeout=60)
        resp.raise_for_status()
    except Exception:
        return {}
    return _supp_labels_from_nxml(resp.content)


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
    """Fetch a paper's supplementary files from Europe PMC.

    Provide exactly one identifier (``pmcid`` preferred; ``pmid`` / ``doi``
    are resolved to a PMCID via the NCBI ID converter). Returns the
    article's supplementary documents — each file that the JATS declares
    as ``<supplementary-material>`` *or* that is itself text-extractable
    (pdf / docx / txt / csv / tsv); loose figure images bundled in the
    ZIP that are neither are skipped. With ``extract=True`` (default) PDF
    / DOCX / text bodies are parsed into :attr:`SuppFile.text`; pass
    ``extract=False`` to list files only.

    Returns ``[]`` when the article isn't open access, has no supplement,
    or the ZIP can't be retrieved. **Open access only** — paywalled
    supplements aren't reachable through this route.
    """
    resolved = _resolve_pmcid(pmcid, pmid, doi)
    if not resolved:
        return []
    blob = _download_supp_zip(resolved)
    time.sleep(_RATE_DELAY)
    if not blob:
        return []
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except Exception:
        return []

    labels = _supp_labels(resolved)

    out: list[SuppFile] = []
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        base = os.path.basename(name)
        if not base:
            continue
        stem = os.path.splitext(base)[0].lower()
        ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
        declared = stem in labels
        text_ext = ext in _KIND_BY_EXT
        if not (declared or text_ext):
            continue  # loose figure image, not a declared supplement
        if extract and text_ext:
            try:
                kind, text = _extract_text(base, zf.read(name))
            except Exception:
                kind, text = _KIND_BY_EXT.get(ext, "other"), ""
        else:
            kind, text = _KIND_BY_EXT.get(ext, "other"), ""
        out.append(
            SuppFile(
                name=base,
                kind=kind,
                label=labels.get(stem, ""),
                text=text,
                n_bytes=zf.getinfo(name).file_size,
            )
        )
    return out

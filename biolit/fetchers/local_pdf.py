"""On-disk PDF fetcher with a DOI-keyed metadata index.

Filenames are unreliable — most reference managers store PDFs under
arbitrary stems (Zotero ``storage/<key>/Document.pdf``, BibDesk,
Better-BibTeX citation keys, raw download names). So the only durable
match is on metadata embedded in or extractable from the PDF itself.

Usage:

1. Build (or update) the index. Re-running the same command is cheap:

       python -m biolit.fetchers.local_pdf --dir ~/Papers
       python -m biolit.fetchers.local_pdf --dir ~/Papers --rebuild

   By default the build is **incremental**: existing entries are
   reused unless the underlying file's ``(mtime, size)`` has changed,
   so re-runs cost a stat per file rather than a full pdfminer pass.
   New PDFs are indexed; deleted PDFs are dropped. Pass ``--rebuild``
   to force a full re-extraction (useful after a pdfminer upgrade or
   if you suspect the cache is corrupt).

   The index is written to
   ``$XDG_CACHE_HOME/biolit/local_pdf_index_<hash>.json`` and
   replaced atomically (via a sibling ``.tmp`` file) so a Ctrl-C
   never leaves a half-written index on disk.

2. Set the env var; biolit will auto-load the fetcher:

       export BIOLIT_LOCAL_PDF_DIR=~/Papers

   The fetcher itself never builds the index — it only looks up DOIs.
   When the index is missing or stale relative to the directory, it
   prints a hint to stderr and returns ``None``, so the rest of the
   built-in fetcher chain still runs.

The fetcher matches on DOI only. PMID-only candidates won't hit unless
their PubMed record includes a DOI by the time biolit calls
``resolve_fulltext`` (the standard ``fetch_pubmed_metadata`` populates
``paper["doi"]`` whenever NCBI has it).

Index schema (v2). The fetcher reads any version that has
``doi_to_path``; the writer always emits both v1 and v2 fields so old
biolit installations keep working::

    {
      "schema_version": 2,
      "directory": "/abs/path",
      "n_pdfs": 6682,
      "n_indexed": 6420,        # entries with a DOI
      "n_unindexed": 262,       # entries without
      "doi_to_path": {<doi>: <path>},   # v1, derived from entries
      "entries": {                       # v2: per-file state
        "<abs path>": {"doi": "10.x/...", "mtime": 1714500000.0, "size": 2116621}
      },
      "unindexed_sample": [...]           # v1, retained for log readability
    }
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ._hooks import FetchContext, FetchResult, register_fetcher

# DOIs are messy. This pattern catches the typical 10.<registrant>/<suffix>
# shape, deliberately conservative on the suffix character class to avoid
# eating trailing punctuation. Callers post-strip with :func:`_clean_doi`.
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
_DOI_TRAILING_STRIP = ".,;:)]}>'\""


def _clean_doi(raw: str) -> str:
    """Lowercase + strip common trailing punctuation."""
    s = raw.strip().rstrip(_DOI_TRAILING_STRIP)
    return s.lower()


def _index_cache_path(directory: Path) -> Path:
    """Stable per-directory cache path under XDG_CACHE_HOME."""
    cache_root = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    digest = hashlib.sha1(str(directory.resolve()).encode("utf-8")).hexdigest()[:12]
    return cache_root / "biolit" / f"local_pdf_index_{digest}.json"


# ---------------------------------------------------------------------------
# DOI extraction from one PDF
# ---------------------------------------------------------------------------


def _doi_from_info_dict(pdf_bytes: bytes) -> str | None:
    """Look for a DOI in a PDF's /Info metadata dict."""
    try:
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdfparser import PDFParser
    except ImportError:
        return None
    import io

    try:
        parser = PDFParser(io.BytesIO(pdf_bytes))
        doc = PDFDocument(parser)
    except Exception:
        return None
    for info in getattr(doc, "info", []):
        for k, v in info.items():
            try:
                key = k.decode("ascii", "ignore") if isinstance(k, bytes) else str(k)
            except Exception:
                continue
            if "doi" not in key.lower() and "identifier" not in key.lower():
                continue
            try:
                val = v.decode("utf-8", "ignore") if isinstance(v, bytes) else str(v)
            except Exception:
                continue
            m = DOI_RE.search(val)
            if m:
                return _clean_doi(m.group(0))
    return None


def _doi_from_first_page(pdf_bytes: bytes) -> str | None:
    """Extract first-page text and regex for a DOI."""
    try:
        from pdfminer.high_level import extract_text
    except ImportError:
        return None
    import io

    try:
        text = extract_text(io.BytesIO(pdf_bytes), maxpages=1)
    except Exception:
        return None
    m = DOI_RE.search(text or "")
    return _clean_doi(m.group(0)) if m else None


def extract_doi(pdf_path: Path) -> str | None:
    """Best-effort DOI extraction from a single PDF file. Returns
    a lowercase DOI string or ``None``."""
    try:
        pdf_bytes = pdf_path.read_bytes()
    except OSError:
        return None
    return _doi_from_info_dict(pdf_bytes) or _doi_from_first_page(pdf_bytes)


# ---------------------------------------------------------------------------
# Index build
# ---------------------------------------------------------------------------


SCHEMA_VERSION = 2


def _load_existing_entries(output_path: Path) -> dict[str, dict]:
    """Read v2 ``entries`` from an existing index, if any. Returns
    ``{abs_path: {"doi": str|None, "mtime": float, "size": int}}``.

    v1 indexes (no ``entries`` block) yield an empty dict — they
    get a full rebuild on first incremental run, which matches what
    they did historically.
    """
    if not output_path.is_file():
        return {}
    try:
        payload = json.loads(output_path.read_text())
    except Exception as e:
        print(f"[local_pdf] cannot read existing index {output_path}: {e}; doing full rebuild", file=sys.stderr)
        return {}
    if not isinstance(payload, dict):
        return {}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return {}
    out: dict[str, dict] = {}
    for path, meta in entries.items():
        if not isinstance(meta, dict):
            continue
        mtime = meta.get("mtime")
        size = meta.get("size")
        doi = meta.get("doi")
        if not isinstance(mtime, (int, float)) or not isinstance(size, int):
            continue
        out[path] = {"doi": doi, "mtime": float(mtime), "size": size}
    return out


def _atomic_write_json(payload: dict, output_path: Path) -> None:
    """Write JSON to ``output_path`` via a ``.tmp`` sibling + rename.
    Avoids a half-written file on Ctrl-C or crash."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, output_path)


def build_index(
    directory: Path,
    output_path: Path | None = None,
    *,
    verbose: bool = True,
    incremental: bool = True,
) -> dict:
    """Walk ``directory`` recursively, extract DOIs, and persist a JSON
    index. Returns the in-memory index payload.

    By default the build is incremental: an existing index is loaded
    and every PDF whose ``(mtime, size)`` matches its prior entry is
    reused without re-running pdfminer. This makes a re-run on an
    unchanged 10k-PDF library cost a few seconds (one stat per file)
    instead of many minutes. Pass ``incremental=False`` to force a
    full re-extraction.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"not a directory: {directory}")
    if output_path is None:
        output_path = _index_cache_path(directory)

    existing = _load_existing_entries(output_path) if incremental else {}

    pdfs = sorted(directory.rglob("*.pdf"))
    if verbose:
        mode = "incremental" if existing else ("rebuild" if not incremental else "first build")
        print(
            f"[local_pdf] {mode}: scanning {len(pdfs)} PDFs under {directory}",
            file=sys.stderr,
        )

    # Decide per-file whether we can reuse the prior DOI extraction.
    #
    # pdfs_to_extract holds the (path, stat) pairs that need pdfminer;
    # everything else is copied straight from `existing`.
    reused: list[tuple[Path, float, int, str | None]] = []     # (path, mtime, size, doi)
    pdfs_to_extract: list[tuple[Path, float, int]] = []
    for pdf in pdfs:
        try:
            st = pdf.stat()
        except OSError:
            continue
        prior = existing.get(str(pdf))
        if prior is not None and prior["mtime"] == st.st_mtime and prior["size"] == st.st_size:
            reused.append((pdf, st.st_mtime, st.st_size, prior["doi"]))
        else:
            pdfs_to_extract.append((pdf, st.st_mtime, st.st_size))

    if verbose and existing:
        print(
            f"[local_pdf] reusing {len(reused)} prior entries; "
            f"extracting {len(pdfs_to_extract)} new/changed PDFs",
            file=sys.stderr,
        )

    iterator: object = pdfs_to_extract
    try:
        from tqdm import tqdm  # type: ignore[import-not-found]
        if verbose and pdfs_to_extract:
            iterator = tqdm(pdfs_to_extract, unit="pdf")
    except ImportError:
        pass

    new_entries: list[tuple[Path, float, int, str | None]] = []
    for pdf, mtime, size in iterator:  # type: ignore[assignment]
        doi = extract_doi(pdf)
        new_entries.append((pdf, mtime, size, doi))

    # Merge.
    entries: dict[str, dict] = {}
    doi_to_path: dict[str, str] = {}
    no_doi: list[str] = []
    for pdf, mtime, size, doi in reused + new_entries:
        path_str = str(pdf)
        entries[path_str] = {"doi": doi, "mtime": mtime, "size": size}
        if doi:
            # First write wins (sorted rglob order is deterministic).
            doi_to_path.setdefault(doi, path_str)
        else:
            no_doi.append(path_str)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "directory": str(directory),
        "n_pdfs": len(pdfs),
        "n_indexed": len(doi_to_path),
        "n_unindexed": len(no_doi),
        "doi_to_path": doi_to_path,
        "entries": entries,
        # Truncated so a 10k-PDF library doesn't bloat the JSON.
        "unindexed_sample": no_doi[:50],
    }
    _atomic_write_json(payload, output_path)
    if verbose:
        print(
            f"[local_pdf] indexed {len(doi_to_path)}/{len(pdfs)} PDFs "
            f"({len(no_doi)} without extractable DOI; "
            f"{len(new_entries)} extracted this run, {len(reused)} reused)\n"
            f"           index: {output_path}",
            file=sys.stderr,
        )
    return payload


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


@dataclass
class LocalPDFFetcher:
    """A fetcher that looks up PDFs in a pre-built DOI index.

    The index is built once via the module CLI (``python -m
    biolit.fetchers.local_pdf --dir ...``) and consulted at lookup time.
    The fetcher does not build it implicitly; we don't want a screening
    run to silently spend many minutes scanning thousands of PDFs.
    """

    directory: Path
    index_path: Path | None = None
    _index_cache: dict[str, str] | None = field(default=None, init=False, repr=False)
    _index_warned: bool = field(default=False, init=False, repr=False)

    @property
    def __name__(self) -> str:
        return "local_pdf"

    def _resolved_index_path(self) -> Path:
        return self.index_path or _index_cache_path(self.directory)

    def _load_index(self) -> dict[str, str]:
        if self._index_cache is not None:
            return self._index_cache
        path = self._resolved_index_path()
        if not path.is_file():
            if not self._index_warned:
                print(
                    f"[local_pdf] no index at {path}\n"
                    f"           build one with: python -m biolit.fetchers.local_pdf --dir {self.directory}",
                    file=sys.stderr,
                )
                self._index_warned = True
            self._index_cache = {}
            return self._index_cache
        try:
            payload = json.loads(path.read_text())
            doi_to_path = payload.get("doi_to_path", {}) if isinstance(payload, dict) else {}
        except Exception as e:
            print(f"[local_pdf] failed to read index {path}: {e}", file=sys.stderr)
            doi_to_path = {}
        self._index_cache = {k.lower(): v for k, v in doi_to_path.items()}
        if self._index_cache:
            print(
                f"[local_pdf] loaded index with {len(self._index_cache)} DOIs from {path}",
                file=sys.stderr,
            )
        return self._index_cache

    def _find(self, paper: dict) -> Path | None:
        doi = (paper.get("doi") or "").strip()
        if not doi:
            return None
        index = self._load_index()
        if not index:
            return None
        path_str = index.get(_clean_doi(doi))
        if not path_str:
            return None
        path = Path(path_str)
        if not path.is_file():
            print(f"[local_pdf] index entry stale (file missing): {path}", file=sys.stderr)
            return None
        return path

    def __call__(self, ctx: FetchContext) -> FetchResult | None:
        from biolit.parsers.pdf import parse_pdf_sections
        from biolit.parsers.utils import DEFAULT_MAX_TOKENS, select_sections

        path = self._find(ctx.paper)
        if path is None:
            return None
        print(f"  [local_pdf] match: {path}", file=sys.stderr)
        try:
            pdf_bytes = path.read_bytes()
        except OSError as e:
            print(f"  [local_pdf] cannot read {path}: {e}", file=sys.stderr)
            return None
        try:
            sections = parse_pdf_sections(pdf_bytes)
        except ImportError:
            return None
        if not sections:
            print(f"  [local_pdf] {path.name}: parse_pdf_sections returned nothing", file=sys.stderr)
            return None
        text = select_sections(
            sections,
            ctx.sections_wanted,
            ctx.max_tokens or DEFAULT_MAX_TOKENS,
        )
        return FetchResult(
            text=text,
            source="local_pdf",
            artifacts={"local_pdf": pdf_bytes},
        )


# ---------------------------------------------------------------------------
# Auto-load and module CLI
# ---------------------------------------------------------------------------


def maybe_autoload() -> bool:
    raw = os.environ.get("BIOLIT_LOCAL_PDF_DIR")
    if not raw:
        return False
    directory = Path(raw).expanduser()
    if not directory.is_dir():
        print(
            f"[local_pdf] BIOLIT_LOCAL_PDF_DIR={directory!s} is not a directory; skipping",
            file=sys.stderr,
        )
        return False
    priority = float(os.environ.get("BIOLIT_LOCAL_PDF_PRIORITY", "3.0"))
    register_fetcher(LocalPDFFetcher(directory=directory), priority=priority, name="local_pdf")
    return True


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m biolit.fetchers.local_pdf",
        description=(
            "Build or update a DOI-keyed index of a directory of PDFs. "
            "Re-running is cheap: by default only new or changed PDFs are "
            "re-extracted. Pass --rebuild to force a full re-extraction."
        ),
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path(os.environ.get("BIOLIT_LOCAL_PDF_DIR", "")) if os.environ.get("BIOLIT_LOCAL_PDF_DIR") else None,
        help="Directory of PDFs (defaults to $BIOLIT_LOCAL_PDF_DIR).",
    )
    parser.add_argument(
        "--index", type=Path, default=None,
        help="Output index path (defaults to a stable XDG cache location).",
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Force a full re-extraction of every PDF, even ones whose mtime is unchanged.",
    )
    args = parser.parse_args(argv)

    if args.dir is None or not args.dir.is_dir():
        print("error: --dir <directory> is required (or set BIOLIT_LOCAL_PDF_DIR)", file=sys.stderr)
        return 2

    out = args.index or _index_cache_path(args.dir.expanduser())
    build_index(args.dir.expanduser(), output_path=out, incremental=not args.rebuild)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli_main())

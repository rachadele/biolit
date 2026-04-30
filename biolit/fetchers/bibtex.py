"""BibTeX-backed full-text fetcher.

Many reference managers (Zotero with Better BibTeX, JabRef, classic
BibTeX workflows) maintain a ``.bib`` export that already records, per
entry, both a structured ``doi`` field and a ``file`` field pointing at
the PDF on disk. That export is everything biolit needs to skip the
network: a ``{doi → file_path}`` map, plus a ``{citekey → file_path}``
map for callers that prefer to look up by citekey.

Compared with the Zotero web fetcher, the BibTeX fetcher has three
advantages for users who already maintain a BibTeX export:

* No network round-trip — lookup is in-memory after a one-time parse.
* Works around the Zotero web API's q-search not indexing the
  structured ``DOI`` field (which makes DOI lookups via that API
  unreliable for items where the DOI doesn't appear in indexed
  attachment full-text).
* Falls through to the local-PDF and built-in chain unchanged when no
  match is found — there's no behavioural cost to enabling it.

Configuration is via environment variables — when ``BIOLIT_BIBTEX`` is
set to a path, this fetcher is auto-registered with priority 2 (i.e.
tried before ``local_pdf`` at priority 3 and ``zotero`` at priority 5).

Required environment:

* ``BIOLIT_BIBTEX`` — path to a ``.bib`` file. ``~`` is expanded.

Optional:

* ``BIOLIT_BIBTEX_PRIORITY`` — float overriding the default priority
  of 2.0.

The parser is intentionally narrow: it scans for ``@<type>{<citekey>,``
headers and extracts only the ``doi``, ``pmid``, and ``file`` fields
from each entry. It does not try to be a general-purpose BibTeX parser
— anything more elaborate is the user's BibTeX export's responsibility,
not biolit's.

The ``file`` field accepts both BBT's ``{path1;path2}`` semicolon-list
and the classic JabRef ``{description:path:type}`` form. The first
``.pdf``-extension component wins.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ._hooks import FetchContext, FetchResult, register_fetcher

# Match the start of an entry: @<type>{<citekey>,
#   <type> is letters/digits, <citekey> can contain almost anything
#   except whitespace, comma, brace.
_ENTRY_HEADER = re.compile(
    r"@(?P<type>[A-Za-z]+)\s*\{\s*(?P<citekey>[^,\s{}]+)\s*,",
    re.MULTILINE,
)

_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
_FIELD_DELIMS = set(" \t\n\r,")


def _read_braced_value(s: str, start: int) -> tuple[str, int] | None:
    """Read a ``{...}`` (balanced braces) or ``"..."`` value starting at
    position ``start``. Returns ``(value, end_index)`` or ``None`` if
    no value can be parsed.
    """
    n = len(s)
    if start >= n:
        return None
    ch = s[start]
    if ch == "{":
        depth = 1
        i = start + 1
        while i < n and depth > 0:
            c = s[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[start + 1:i], i + 1
            i += 1
        return None
    if ch == '"':
        i = start + 1
        while i < n:
            if s[i] == '"' and s[i - 1] != "\\":
                return s[start + 1:i], i + 1
            i += 1
        return None
    # Numeric or unquoted string value — terminated by comma, newline,
    # or the entry's closing brace.
    end = start
    while end < n and s[end] not in ",\n}":
        end += 1
    return s[start:end].strip(), end


def _parse_fields(entry_body: str) -> dict[str, str]:
    """Pull ``doi``, ``pmid``, ``file`` (case-insensitive) out of an
    entry body. Walks the body positionally so it works for both
    one-field-per-line BBT exports and single-line entries (``key =
    {val}, key = {val}``).
    """
    wanted = {"doi", "pmid", "file"}
    out: dict[str, str] = {}
    n = len(entry_body)
    i = 0
    while i < n:
        # Skip whitespace and field separators.
        while i < n and entry_body[i] in _FIELD_DELIMS:
            i += 1
        if i >= n or entry_body[i] == "}":
            break
        # Read field name.
        name_start = i
        while i < n and entry_body[i] in _NAME_CHARS:
            i += 1
        if i == name_start:
            # Unexpected character (e.g. a stray comment); skip and try again.
            i += 1
            continue
        name = entry_body[name_start:i].lower()
        # Skip whitespace before `=`.
        while i < n and entry_body[i] in " \t":
            i += 1
        if i >= n or entry_body[i] != "=":
            continue
        i += 1
        while i < n and entry_body[i] in " \t":
            i += 1
        parsed = _read_braced_value(entry_body, i)
        if parsed is None:
            break
        value, i = parsed
        if name in wanted and name not in out:
            out[name] = value.strip()
        if wanted.issubset(out.keys()):
            break
    return out


def _pick_pdf_path(file_field: str) -> str | None:
    """From a BibTeX ``file = {...}`` value, return the first path
    that ends in ``.pdf``. Handles both BBT semicolon-separated lists
    and classic JabRef ``description:path:type`` triples.

    Returns an absolute (or as-stored) path string, or ``None``.
    """
    if not file_field:
        return None
    for chunk in file_field.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        # JabRef triples: description:path:type. The path is the middle
        # field, but ``description`` and ``type`` are usually empty in
        # practice — strip the leading/trailing colon if present.
        if chunk.count(":") >= 2 and not _looks_like_absolute_path(chunk):
            parts = chunk.split(":")
            # description : path : type → 3+ parts; path is parts[1] or
            # the longest non-empty part.
            candidates = [p for p in parts if p.strip().lower().endswith(".pdf")]
            if candidates:
                return candidates[0].strip()
            # Fallback: middle slot.
            if len(parts) >= 2 and parts[1].strip():
                if parts[1].strip().lower().endswith(".pdf"):
                    return parts[1].strip()
            continue
        if chunk.lower().endswith(".pdf"):
            return chunk
    return None


def _looks_like_absolute_path(s: str) -> bool:
    """Best-effort: does ``s`` start with an absolute-path indicator?
    Distinguishes BBT-style ``/Users/...`` and Windows ``C:\\...`` from
    JabRef ``description:/Users/...`` triples that also contain colons.
    """
    return s.startswith(("/", "~")) or (len(s) >= 3 and s[1:3] == ":\\")


def _clean_doi(raw: str) -> str:
    """Lowercase + strip surrounding whitespace and trailing
    punctuation. Mirrors the policy in :mod:`biolit.fetchers.local_pdf`
    so the two fetchers index DOIs consistently."""
    s = (raw or "").strip()
    return s.rstrip(".,;:)]}>'\"").lower()


@dataclass
class BibTeXIndex:
    """Parsed view of a ``.bib`` file. Built lazily and cached on the
    fetcher; refreshed when the source file's mtime changes.
    """

    by_doi: dict[str, str]            # doi (lowercased) → pdf path
    by_pmid: dict[str, str]           # pmid (string) → pdf path
    by_citekey: dict[str, str]        # citekey → pdf path
    n_entries: int                    # for logging
    n_with_pdf: int                   # for logging
    source_mtime: float               # for cache invalidation


def parse_bibtex(text: str) -> BibTeXIndex:
    """Parse ``text`` (BibTeX source) into a :class:`BibTeXIndex`."""
    by_doi: dict[str, str] = {}
    by_pmid: dict[str, str] = {}
    by_citekey: dict[str, str] = {}
    n_entries = 0
    n_with_pdf = 0

    headers = list(_ENTRY_HEADER.finditer(text))
    for i, m in enumerate(headers):
        n_entries += 1
        body_start = m.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[body_start:body_end]

        fields = _parse_fields(body)
        pdf_path = _pick_pdf_path(fields.get("file", ""))
        if not pdf_path:
            continue
        n_with_pdf += 1

        citekey = m.group("citekey").strip()
        if citekey and citekey not in by_citekey:
            by_citekey[citekey] = pdf_path

        doi = _clean_doi(fields.get("doi", ""))
        if doi and doi not in by_doi:
            by_doi[doi] = pdf_path

        pmid = (fields.get("pmid") or "").strip()
        if pmid.isdigit() and pmid not in by_pmid:
            by_pmid[pmid] = pdf_path

    return BibTeXIndex(
        by_doi=by_doi,
        by_pmid=by_pmid,
        by_citekey=by_citekey,
        n_entries=n_entries,
        n_with_pdf=n_with_pdf,
        source_mtime=0.0,
    )


@dataclass
class BibTeXFetcher:
    """A fetcher that resolves DOI/PMID/citekey to a local PDF using a
    BibTeX export.

    The BibTeX file is parsed lazily on the first lookup, cached
    in-memory, and re-parsed automatically when its mtime changes.
    """

    bib_path: Path
    _index: BibTeXIndex | None = field(default=None, init=False, repr=False)
    _warned_missing: bool = field(default=False, init=False, repr=False)

    @property
    def __name__(self) -> str:  # for logging
        return "bibtex"

    def _load(self) -> BibTeXIndex | None:
        path = self.bib_path
        if not path.is_file():
            if not self._warned_missing:
                print(
                    f"[bibtex] BIOLIT_BIBTEX={path} does not exist; fetcher will return None for all lookups",
                    file=sys.stderr,
                )
                self._warned_missing = True
            return None
        try:
            mtime = path.stat().st_mtime
        except OSError as e:
            print(f"[bibtex] cannot stat {path}: {e}", file=sys.stderr)
            return None
        if self._index is not None and self._index.source_mtime == mtime:
            return self._index
        try:
            text = path.read_text(errors="replace")
        except OSError as e:
            print(f"[bibtex] cannot read {path}: {e}", file=sys.stderr)
            return None
        index = parse_bibtex(text)
        index.source_mtime = mtime
        self._index = index
        print(
            f"[bibtex] parsed {index.n_entries} entries from {path} "
            f"({index.n_with_pdf} with a PDF; {len(index.by_doi)} unique DOIs, "
            f"{len(index.by_pmid)} unique PMIDs)",
            file=sys.stderr,
        )
        return index

    def _find(self, paper: dict) -> Path | None:
        index = self._load()
        if index is None:
            return None

        doi = _clean_doi(paper.get("doi") or "")
        if doi:
            hit = index.by_doi.get(doi)
            if hit:
                p = Path(hit)
                if p.is_file():
                    return p
                print(f"[bibtex] index entry stale (file missing): {p}", file=sys.stderr)

        pmid = (paper.get("pmid") or "").strip()
        if pmid and pmid.isdigit():
            hit = index.by_pmid.get(pmid)
            if hit:
                p = Path(hit)
                if p.is_file():
                    return p
                print(f"[bibtex] index entry stale (file missing): {p}", file=sys.stderr)

        citekey = (paper.get("citekey") or "").strip()
        if citekey:
            hit = index.by_citekey.get(citekey)
            if hit:
                p = Path(hit)
                if p.is_file():
                    return p
                print(f"[bibtex] index entry stale (file missing): {p}", file=sys.stderr)

        return None

    def __call__(self, ctx: FetchContext) -> FetchResult | None:
        from biolit.parsers.pdf import parse_pdf_sections
        from biolit.parsers.utils import DEFAULT_MAX_TOKENS, select_sections

        path = self._find(ctx.paper)
        if path is None:
            return None
        print(f"  [bibtex] match: {path}", file=sys.stderr)
        try:
            pdf_bytes = path.read_bytes()
        except OSError as e:
            print(f"  [bibtex] cannot read {path}: {e}", file=sys.stderr)
            return None
        try:
            sections = parse_pdf_sections(pdf_bytes)
        except ImportError:
            return None
        if not sections:
            print(f"  [bibtex] {path.name}: parse_pdf_sections returned nothing", file=sys.stderr)
            return None
        text = select_sections(
            sections,
            ctx.sections_wanted,
            ctx.max_tokens or DEFAULT_MAX_TOKENS,
        )
        return FetchResult(
            text=text,
            source="bibtex_pdf",
            artifacts={"bibtex_pdf": pdf_bytes},
        )


def maybe_autoload() -> bool:
    """Register a :class:`BibTeXFetcher` from env vars. Returns True
    iff configured."""
    raw = os.environ.get("BIOLIT_BIBTEX")
    if not raw:
        return False
    bib_path = Path(raw).expanduser()
    if not bib_path.is_file():
        print(
            f"[bibtex] BIOLIT_BIBTEX={bib_path} is not a file; skipping",
            file=sys.stderr,
        )
        return False
    priority = float(os.environ.get("BIOLIT_BIBTEX_PRIORITY", "2.0"))
    register_fetcher(BibTeXFetcher(bib_path=bib_path), priority=priority, name="bibtex")
    return True

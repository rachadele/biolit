"""Zotero full-text fetcher.

Looks up a paper in the user's Zotero library by DOI or PMID, finds an
attached PDF, downloads it, and parses it via biolit's PDF parser. Useful
when the Zotero library has manually-added PDFs for papers that PMC /
Europe PMC / Unpaywall don't have open-access copies of (typical for
recent or paywalled work).

Configuration: env vars take precedence; missing values fall back to the
platform credential store used by biolit's LLM key resolution
(``biolit.llm.base.resolve_api_key`` — macOS keychain via ``security
find-generic-password -s <NAME> -w``; on non-darwin platforms only env
vars are consulted). When ``ZOTERO_API_KEY`` and either ``ZOTERO_USER_ID``
or ``ZOTERO_GROUP_ID`` resolve from any source, this fetcher is
auto-registered with priority 5 (i.e. tried before the PMC chain).

Required (env or keychain):

* ``ZOTERO_API_KEY`` — a Zotero web API key with read access.
* Either ``ZOTERO_USER_ID`` or ``ZOTERO_GROUP_ID`` (or both).

Optional:

* ``ZOTERO_PRIORITY`` — float overriding the default priority of 5.0.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import requests

from biolit.llm.base import resolve_api_key

from ._hooks import FetchContext, FetchResult, register_fetcher

ZOTERO_API_BASE = "https://api.zotero.org"
# Default Zotero data directory on macOS/Linux. Override with
# ZOTERO_DATA_DIR if the user has configured Zotero to store its profile
# elsewhere. The actual files live under ``<data_dir>/storage/<key>/``.
DEFAULT_ZOTERO_DATA_DIR = "~/Zotero"


@dataclass
class ZoteroFetcher:
    """A Zotero-backed fetcher. Registers itself when configured via env."""

    api_key: str
    user_id: str | None = None
    group_id: str | None = None
    timeout: int = 20

    @property
    def __name__(self) -> str:  # for logging
        return "zotero"

    def _api_base(self) -> str:
        if self.group_id:
            return f"{ZOTERO_API_BASE}/groups/{self.group_id}"
        if self.user_id:
            return f"{ZOTERO_API_BASE}/users/{self.user_id}"
        raise RuntimeError("ZoteroFetcher needs either user_id or group_id")

    def _headers(self) -> dict:
        return {"Zotero-API-Key": self.api_key, "Zotero-API-Version": "3"}

    def _search_by_query(self, query: str) -> list[dict]:
        """Free-text search for an item; results contain the item key."""
        r = requests.get(
            f"{self._api_base()}/items",
            params={"q": query, "qmode": "everything", "limit": 5},
            headers=self._headers(),
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def _children(self, item_key: str) -> list[dict]:
        r = requests.get(
            f"{self._api_base()}/items/{item_key}/children",
            headers=self._headers(),
            timeout=self.timeout,
        )
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return r.json()

    def _get_item(self, item_key: str) -> dict | None:
        r = requests.get(
            f"{self._api_base()}/items/{item_key}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def _download_attachment(self, attachment_key: str, filename: str | None = None) -> bytes | None:
        r = requests.get(
            f"{self._api_base()}/items/{attachment_key}/file",
            headers=self._headers(),
            timeout=self.timeout,
            allow_redirects=True,
        )
        if r.status_code == 200:
            return r.content
        # The /file endpoint only serves attachments uploaded to Zotero's
        # cloud storage. linked_file attachments and imported_file
        # attachments on accounts without sync return 404. Fall back to
        # the user's local Zotero storage tree.
        if filename:
            data_dir = Path(os.environ.get("ZOTERO_DATA_DIR") or DEFAULT_ZOTERO_DATA_DIR).expanduser()
            local = data_dir / "storage" / attachment_key / filename
            try:
                pdf_bytes = local.read_bytes()
            except OSError:
                return None
            print(f"  [zotero] /file returned {r.status_code}; read from {local}", file=sys.stderr)
            return pdf_bytes
        return None

    def _find_item(self, paper: dict) -> dict | None:
        """Best-effort match by DOI then PMID. Returns the first item
        whose DOI or extra-field PMID matches what we asked for.

        ``qmode=everything`` searches attachment full-text too, so hits are
        often the PDF attachment of a *different* paper that happens to
        cite the DOI we asked about. To match correctly we resolve every
        attachment hit to its parent item and compare against the parent's
        DOI / extra fields.
        """
        doi = (paper.get("doi") or "").strip()
        pmid = (paper.get("pmid") or "").strip()

        for query in (doi, pmid):
            if not query:
                continue
            try:
                hits = self._search_by_query(query)
            except Exception as e:
                print(f"  [zotero] search error: {e}", file=sys.stderr)
                continue
            for hit in hits:
                data = hit.get("data", {})
                if data.get("itemType") == "attachment" and data.get("parentItem"):
                    try:
                        parent = self._get_item(data["parentItem"])
                    except Exception as e:
                        print(f"  [zotero] parent fetch error: {e}", file=sys.stderr)
                        continue
                    if parent is None:
                        continue
                    candidate = parent
                else:
                    candidate = hit
                cdata = candidate.get("data", {})
                cand_doi = (cdata.get("DOI") or "").lower()
                cand_extra = (cdata.get("extra") or "").lower()
                if doi and cand_doi == doi.lower():
                    return candidate
                if pmid and (f"pmid: {pmid}" in cand_extra or f"pmid:{pmid}" in cand_extra):
                    return candidate
        return None

    def __call__(self, ctx: FetchContext) -> FetchResult | None:
        # Avoid circular imports — these come from the same package but
        # importing at module load time creates an import cycle.
        from biolit.parsers.pdf import parse_pdf_sections
        from biolit.parsers.utils import DEFAULT_MAX_TOKENS, select_sections

        item = self._find_item(ctx.paper)
        if item is None:
            return None

        item_key = item.get("key")
        if not item_key:
            return None

        for child in self._children(item_key):
            data = child.get("data", {})
            if data.get("itemType") != "attachment":
                continue
            if (data.get("contentType") or "").lower() != "application/pdf":
                continue
            pdf_bytes = self._download_attachment(child["key"], filename=data.get("filename"))
            if not pdf_bytes:
                continue
            try:
                sections = parse_pdf_sections(pdf_bytes)
            except ImportError:
                # pdfminer.six not installed — let the caller fall through.
                return None
            if not sections:
                continue
            text = select_sections(
                sections,
                ctx.sections_wanted,
                ctx.max_tokens or DEFAULT_MAX_TOKENS,
            )
            return FetchResult(
                text=text,
                source="zotero_pdf",
                artifacts={"zotero_pdf": pdf_bytes},
            )
        return None


def _resolve_zotero_credentials() -> tuple[str | None, str | None, str | None]:
    """Return ``(api_key, user_id, group_id)`` from env vars with a keychain fallback.

    Each value is resolved via :func:`biolit.llm.base.resolve_api_key`, which
    checks the macOS keychain first and falls back to the env var. On
    non-darwin platforms the keychain step is skipped and only the env var
    is consulted. The helper is silent on missing tools or non-zero
    ``security`` exits — failures degrade to ``None`` rather than raising.
    """
    return (
        resolve_api_key("ZOTERO_API_KEY"),
        resolve_api_key("ZOTERO_USER_ID"),
        resolve_api_key("ZOTERO_GROUP_ID"),
    )


def maybe_autoload() -> bool:
    """Register ``ZoteroFetcher`` if credentials are available. Returns True if registered.

    Credentials are resolved by :func:`_resolve_zotero_credentials` (env vars
    first, then the platform credential store), so callers running outside a
    shell that exports ``ZOTERO_*`` (e.g. an MCP host) can still pick up
    keys stored in the macOS keychain.
    """
    api_key, user_id, group_id = _resolve_zotero_credentials()
    if not api_key or not (user_id or group_id):
        return False
    priority = float(os.environ.get("ZOTERO_PRIORITY", "5.0"))
    register_fetcher(
        ZoteroFetcher(api_key=api_key, user_id=user_id, group_id=group_id),
        priority=priority,
        name="zotero",
    )
    return True

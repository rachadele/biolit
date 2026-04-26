"""Zotero full-text fetcher.

Looks up a paper in the user's Zotero library by DOI or PMID, finds an
attached PDF, downloads it, and parses it via biolit's PDF parser. Useful
when the Zotero library has manually-added PDFs for papers that PMC /
Europe PMC / Unpaywall don't have open-access copies of (typical for
recent or paywalled work).

Configuration is via environment variables — when ``ZOTERO_API_KEY`` and
``ZOTERO_USER_ID`` are set, this fetcher is auto-registered with priority
5 (i.e. tried before the PMC chain). A group library can be searched
instead of (or in addition to) the user library by setting
``ZOTERO_GROUP_ID``.

Required environment:

* ``ZOTERO_API_KEY`` — a Zotero web API key with read access.
* Either ``ZOTERO_USER_ID`` or ``ZOTERO_GROUP_ID`` (or both).

Optional:

* ``ZOTERO_PRIORITY`` — float overriding the default priority of 5.0.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import requests

from ._hooks import FetchContext, FetchResult, register_fetcher

ZOTERO_API_BASE = "https://api.zotero.org"


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

    def _download_attachment(self, attachment_key: str) -> bytes | None:
        r = requests.get(
            f"{self._api_base()}/items/{attachment_key}/file",
            headers=self._headers(),
            timeout=self.timeout,
            allow_redirects=True,
        )
        if r.status_code != 200:
            return None
        return r.content

    def _find_item(self, paper: dict) -> dict | None:
        """Best-effort match by DOI then PMID. Returns the first hit whose
        DOI or extra-field PMID matches what we asked for."""
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
                hit_doi = (data.get("DOI") or "").lower()
                hit_extra = (data.get("extra") or "").lower()
                if doi and hit_doi == doi.lower():
                    return hit
                if pmid and (f"pmid: {pmid}" in hit_extra or f"pmid:{pmid}" in hit_extra):
                    return hit
            # Soft fallback: take the first hit if exactly one was returned.
            if len(hits) == 1:
                return hits[0]
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
            pdf_bytes = self._download_attachment(child["key"])
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


def maybe_autoload() -> bool:
    """Register ``ZoteroFetcher`` from env vars. Returns True if registered."""
    api_key = os.environ.get("ZOTERO_API_KEY")
    user_id = os.environ.get("ZOTERO_USER_ID")
    group_id = os.environ.get("ZOTERO_GROUP_ID")
    if not api_key or not (user_id or group_id):
        return False
    priority = float(os.environ.get("ZOTERO_PRIORITY", "5.0"))
    register_fetcher(
        ZoteroFetcher(api_key=api_key, user_id=user_id, group_id=group_id),
        priority=priority,
        name="zotero",
    )
    return True

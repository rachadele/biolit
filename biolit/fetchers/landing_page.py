"""Scrape a publisher landing page for an open-access PDF link.

This mirrors the highest-value step of Zotero's "Find Full Text" file
resolvers: resolve a DOI (or item URL) to the publisher's article
**landing page**, then read the PDF link the page itself advertises. The
de-facto standard is the Highwire / Google-Scholar
``<meta name="citation_pdf_url">`` tag, which the great majority of
publishers embed. That link frequently catches an OA PDF that the
aggregator APIs (Unpaywall / OpenAlex / Europe PMC / CORE) mislabel or
never index, because it comes straight from the publisher's own metadata.

We look, in priority order, for:

  1. ``<meta name="citation_pdf_url" content=...>`` (Highwire standard)
  2. ``<link rel="alternate" type="application/pdf" href=...>``
  3. Open Graph / Twitter-card PDF pointers (``<meta property=...>``
     whose name/property mentions "pdf")
  4. Obvious ``*.pdf`` anchors on the page

A ``<meta http-equiv="refresh">`` redirect is followed once. Every
candidate is downloaded and verified to start with the ``%PDF`` magic
before being returned, so HTML interstitials never slip through.

ToS note: this follows the publisher's *own advertised*
``citation_pdf_url`` for open-access content — it is not a paywall
bypass. bioRxiv / medRxiv are skipped because their servers block
automated agents; the preprint JATS fetcher covers those instead.

Source label: ``landing_page_pdf``.
"""
import os
import re
from urllib.parse import urljoin, urlparse

import requests

try:  # lxml is a hard biolit dependency; this is belt-and-braces.
    from lxml import html as _lxml_html
except Exception:  # pragma: no cover - lxml is always installed
    _lxml_html = None


# A realistic desktop-browser User-Agent. Many publishers serve a bare
# metadata stub (or a 403) to non-browser agents, so we present as one.
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Preprint servers that block automated agents — skip them entirely.
_BLOCKED_HOSTS = ("biorxiv.org", "medrxiv.org")
_BLOCKED_DOI_PREFIXES = ("10.1101/",)

_META_REFRESH_URL = re.compile(r"url\s*=\s*['\"]?([^'\";]+)", re.IGNORECASE)


def _user_agent(user_agent: str | None) -> str:
    return (
        user_agent
        or os.environ.get("BIOLIT_LANDING_USER_AGENT")
        or _DEFAULT_USER_AGENT
    )


def _is_blocked(target: str, doi: str | None) -> bool:
    if doi:
        doi_l = doi.lower().lstrip("/")
        if any(doi_l.startswith(p) for p in _BLOCKED_DOI_PREFIXES):
            return True
    host = (urlparse(target).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _BLOCKED_HOSTS)


def _meta_refresh_target(doc, base_url: str) -> str | None:
    """Return the absolute URL of a ``<meta http-equiv=refresh>`` redirect."""
    for meta in doc.xpath("//meta[@http-equiv]"):
        if (meta.get("http-equiv") or "").strip().lower() != "refresh":
            continue
        m = _META_REFRESH_URL.search(meta.get("content") or "")
        if m:
            return urljoin(base_url, m.group(1).strip())
    return None


def _pdf_candidates(doc, base_url: str) -> list[str]:
    """Collect candidate PDF URLs from a parsed landing page, best first."""
    candidates: list[str] = []

    def _add(href: str | None) -> None:
        if href and href.strip():
            candidates.append(urljoin(base_url, href.strip()))

    # 1. Highwire / Google-Scholar citation_pdf_url (the gold standard).
    for href in doc.xpath(
        "//meta[translate(@name,'CITPDFURL','citpdfurl')='citation_pdf_url']/@content"
    ):
        _add(href)

    # 2. <link rel="alternate" type="application/pdf">.
    for link in doc.xpath("//link[@type]"):
        if "application/pdf" in (link.get("type") or "").lower():
            _add(link.get("href"))

    # 3. Open Graph / Twitter-card style PDF pointers.
    for meta in doc.xpath("//meta[@content]"):
        key = ((meta.get("property") or meta.get("name") or "")).lower()
        if not key or key == "citation_pdf_url":
            continue
        content = meta.get("content") or ""
        if "pdf" in key or content.lower().split("?")[0].endswith(".pdf"):
            _add(content)

    # 4. Obvious *.pdf anchors.
    for href in doc.xpath("//a[@href]/@href"):
        path = urlparse(href).path.lower()
        if path.endswith(".pdf") or ".pdf?" in href.lower():
            _add(href)

    # De-dup, preserve order.
    seen: set[str] = set()
    deduped: list[str] = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def fetch_via_landing_page(
    doi: str | None = None,
    url: str | None = None,
    *,
    user_agent: str | None = None,
) -> bytes | None:
    """Resolve *doi* / *url* to a landing page and download its OA PDF.

    Fetches ``https://doi.org/{doi}`` (or *url* directly), follows
    redirects to the article page, and reads the PDF link the page
    advertises — ``citation_pdf_url`` first, then ``<link
    rel=alternate>``, then OG/Twitter PDF pointers, then ``*.pdf``
    anchors. A ``<meta http-equiv=refresh>`` redirect is followed once.

    *user_agent* overrides the default browser User-Agent (also settable
    via the ``BIOLIT_LANDING_USER_AGENT`` environment variable).

    Returns raw PDF bytes (verified to start with ``%PDF``), or None if
    no advertised PDF is found, the target is a blocked preprint server,
    or any network / parsing error occurs. Never raises.
    """
    if not doi and not url:
        return None
    if _lxml_html is None:  # pragma: no cover
        return None

    target = url or f"https://doi.org/{doi}"
    if _is_blocked(target, doi):
        return None

    headers = {
        "User-Agent": _user_agent(user_agent),
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
    }

    try:
        resp = requests.get(target, headers=headers, timeout=20, allow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return None

    # The DOI may redirect straight to the PDF.
    content = resp.content or b""
    if content[:4] == b"%PDF":
        return content

    # Bail if the redirect chain landed on a blocked preprint host.
    final_url = getattr(resp, "url", None) or target
    if _is_blocked(final_url, None):
        return None

    try:
        doc = _lxml_html.fromstring(content)
    except Exception:
        return None

    # Follow a single meta-refresh redirect, then re-parse.
    refresh = _meta_refresh_target(doc, final_url)
    if refresh and refresh != final_url:
        try:
            resp = requests.get(refresh, headers=headers, timeout=20, allow_redirects=True)
            resp.raise_for_status()
            content = resp.content or b""
            if content[:4] == b"%PDF":
                return content
            final_url = getattr(resp, "url", None) or refresh
            if _is_blocked(final_url, None):
                return None
            doc = _lxml_html.fromstring(content)
        except Exception:
            return None

    for pdf_url in _pdf_candidates(doc, final_url):
        try:
            pdf_resp = requests.get(pdf_url, headers=headers, timeout=60, allow_redirects=True)
            pdf_resp.raise_for_status()
        except Exception:
            continue
        body = pdf_resp.content or b""
        if body[:4] == b"%PDF":
            return body
    return None

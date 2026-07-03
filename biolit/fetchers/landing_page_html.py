"""Recover article body *text* (not a PDF) from a publisher landing page.

This is the sibling of :mod:`biolit.fetchers.landing_page`. Where that module
hunts for a downloadable ``citation_pdf_url`` PDF, this one extracts the HTML
full text directly — the main pipeline win for open-access papers that have a
full HTML version but no downloadable PDF (PLOS, eLife, BMC, Frontiers, many
society journals). Such papers are exactly where Methods text lives, so pulling
the HTML body recovers content the PDF-only chain misses entirely.

Strategy (ported from the "Better Find Full Text" Zotero plugin):

  1. Resolve ``https://doi.org/{doi}`` (or *url*) to the article page.
  2. Read ``<meta name="citation_fulltext_html_url">`` — the Highwire /
     Google-Scholar "a full HTML version exists" signal that OA publishers
     set — and fetch that page if it differs from the landing page.
  3. Extract the article body text: drop nav / scripts / styles / chrome,
     prefer an ``<article>`` / ``<main>`` container, fall back to ``<body>``.

bioRxiv / medRxiv are skipped (their servers block agents; the preprint JATS
fetcher covers them). Bot-challenge pages and JS-rendered shells are rejected
via :mod:`biolit.fetchers.page_classify` rather than returned as "text".

Source label: ``landing_page_html``. Returns extracted article text, or None
on any miss / network / parse error. Never raises.
"""
import re

import requests

from biolit.fetchers.landing_page import (
    _is_blocked,
    _meta_refresh_target,
    _user_agent,
)
from biolit.fetchers.page_classify import (
    STATUS_ABSTRACT,
    classify_html_status,
    has_substantial_content,
    is_bot_challenge,
)

try:  # lxml is a hard biolit dependency; this is belt-and-braces.
    from lxml import html as _lxml_html
except Exception:  # pragma: no cover - lxml is always installed
    _lxml_html = None


# Elements whose text is chrome, not article content.
_STRIP_TAGS = (
    "script", "style", "noscript", "nav", "header", "footer",
    "aside", "form", "button", "svg", "iframe",
)

# Container elements that hold the article body, best first.
_CONTAINER_XPATHS = (
    "//*[local-name()='article']",
    "//*[local-name()='main']",
    "//*[@role='main']",
    "//*[@id='content']",
    "//*[@id='main-content']",
    "//*[@id='article']",
)

_BLANK_LINES_RE = re.compile(r"\n\s*\n\s*\n+")
_INLINE_WS_RE = re.compile(r"[ \t ]+")


def _headers(user_agent: str | None) -> dict:
    return {
        "User-Agent": _user_agent(user_agent),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    }


def _get_html(target: str, headers: dict) -> tuple[str | None, str | None]:
    """GET *target*, follow one meta-refresh, and return (html, final_url).

    Returns ``(None, None)`` on any network error or a non-HTML / blocked
    landing. Never raises.
    """
    try:
        resp = requests.get(target, headers=headers, timeout=20, allow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return None, None

    final_url = getattr(resp, "url", None) or target
    if _is_blocked(final_url, None):
        return None, None
    try:
        html = resp.content.decode(resp.encoding or "utf-8", errors="replace")
    except Exception:
        try:
            html = resp.text
        except Exception:
            return None, None

    if _lxml_html is None:  # pragma: no cover
        return html, final_url

    # Follow a single <meta http-equiv=refresh> redirect, then re-fetch.
    try:
        doc = _lxml_html.fromstring(html)
        refresh = _meta_refresh_target(doc, final_url)
    except Exception:
        refresh = None
    if refresh and refresh != final_url:
        try:
            resp = requests.get(refresh, headers=headers, timeout=20, allow_redirects=True)
            resp.raise_for_status()
            final_url = getattr(resp, "url", None) or refresh
            if _is_blocked(final_url, None):
                return None, None
            html = resp.content.decode(resp.encoding or "utf-8", errors="replace")
        except Exception:
            return html, final_url
    return html, final_url


def _citation_html_url(doc, base_url: str) -> str | None:
    """Read ``<meta name='citation_fulltext_html_url'>`` (case-insensitive)."""
    from urllib.parse import urljoin

    hrefs = doc.xpath(
        "//meta[translate(@name,"
        "'CITAONFULLEXTHMRPS','citaonfullexthmrps')"
        "='citation_fulltext_html_url']/@content"
    )
    for href in hrefs:
        if href and href.strip():
            return urljoin(base_url, href.strip())
    return None


def _extract_article_text(html: str) -> str:
    """Extract clean article body text from an HTML document string.

    Drops chrome (nav / scripts / styles / forms), prefers an ``<article>`` /
    ``<main>`` container, falls back to ``<body>``, then collapses whitespace.
    Returns ``""`` if nothing parseable.
    """
    if _lxml_html is None or not html:  # pragma: no cover
        return ""
    try:
        doc = _lxml_html.fromstring(html)
    except Exception:
        return ""

    # Remove chrome elements wholesale.
    strip_xpath = "//*[" + " or ".join(f"local-name()='{t}'" for t in _STRIP_TAGS) + "]"
    try:
        for el in doc.xpath(strip_xpath):
            el.drop_tree()
    except Exception:
        pass

    container = None
    for xp in _CONTAINER_XPATHS:
        try:
            nodes = doc.xpath(xp)
        except Exception:
            nodes = []
        if nodes:
            container = nodes[0]
            break
    if container is None:
        body = doc.xpath("//*[local-name()='body']")
        container = body[0] if body else doc

    try:
        text = container.text_content()
    except Exception:
        return ""

    text = text.replace(" ", " ")
    text = "\n".join(_INLINE_WS_RE.sub(" ", line).strip() for line in text.splitlines())
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def fetch_landing_page_html(
    doi: str | None = None,
    url: str | None = None,
    *,
    user_agent: str | None = None,
) -> str | None:
    """Resolve *doi* / *url* to a landing page and extract its article text.

    Fetches ``https://doi.org/{doi}`` (or *url*), follows the publisher's
    ``citation_fulltext_html_url`` when present (the "full HTML version exists"
    signal), and extracts the article body text. Returns the extracted text, or
    None if the target is a blocked preprint server, the page is a bot
    challenge / JS shell, no substantial text is found, or any network / parse
    error occurs. Never raises.

    *user_agent* overrides the default browser User-Agent (also settable via
    ``BIOLIT_LANDING_USER_AGENT``).
    """
    if not doi and not url:
        return None
    if _lxml_html is None:  # pragma: no cover
        return None

    target = url or f"https://doi.org/{doi}"
    if _is_blocked(target, doi):
        return None

    headers = _headers(user_agent)
    html, final_url = _get_html(target, headers)
    if not html:
        return None
    if is_bot_challenge(html):
        return None

    # Prefer the publisher's advertised full-HTML page when it differs.
    try:
        doc = _lxml_html.fromstring(html)
        html_url = _citation_html_url(doc, final_url or target)
    except Exception:
        html_url = None
    if html_url and html_url != final_url:
        alt_html, alt_url = _get_html(html_url, headers)
        if alt_html and not is_bot_challenge(alt_html):
            html, final_url = alt_html, alt_url

    text = _extract_article_text(html)
    # Guard against returning a thin shell as if it were full text.
    if not text or not has_substantial_content(text):
        return None
    return text


def classify_landing_page(
    doi: str | None = None,
    url: str | None = None,
    *,
    user_agent: str | None = None,
) -> str:
    """Fetch the landing page and return *why* it is not full text.

    Returns one of the :mod:`biolit.fetchers.page_classify` status labels
    (``bot_blocked`` / ``js_shell`` / ``abstract`` / ``fulltext``), or
    ``abstract`` as a safe default when the page cannot be fetched. Lets a
    caller (e.g. the gemma pipeline's ``paper_status`` field) record the
    reason full text was not reached. Never raises.
    """
    if not doi and not url:
        return STATUS_ABSTRACT
    target = url or f"https://doi.org/{doi}"
    if _is_blocked(target, doi):
        return STATUS_ABSTRACT
    if _lxml_html is None:  # pragma: no cover
        return STATUS_ABSTRACT

    html, final_url = _get_html(target, _headers(user_agent))
    if not html:
        return STATUS_ABSTRACT
    return classify_html_status(html, final_url or target)

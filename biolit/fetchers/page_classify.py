"""Heuristics that classify *why* a fetched landing page is not usable full text.

Ported from the "Better Find Full Text" Zotero plugin's content-quality
checks. These are pure-string predicates — no network, no lxml — so they are
cheap, deterministic, and safe to run on any HTML the resolvers already hold.

The landing-page resolvers use them to return a more precise NON-fulltext
outcome than a blanket ``abstract``: a Cloudflare interstitial is
``bot_blocked``, a JavaScript-rendered shell is ``js_shell``, and a publisher
abstract stub is ``abstract``. A caller (e.g. the gemma curation pipeline's
``paper_status`` field) can then read *why* full text was not reached.

Public API:

  - :func:`is_bot_challenge` — Cloudflare / "Just a moment" challenge markers.
  - :func:`has_substantial_content` — visible-char count above the JS-shell
    threshold (configurable via ``BIOLIT_JS_SHELL_CHAR_THRESHOLD``).
  - :func:`is_abstract_only_url` — URL-shape patterns for abstract-only pages.
  - :func:`classify_html_status` — combinator returning one status label.
"""
import os
import re

# Default visible-character floor below which raw HTML is treated as a
# JS-rendered shell (cookie banner + nav chrome, no server-rendered article
# body). Real article bodies run into the tens of thousands of characters;
# SPA shells (a React PubMed page, a cookie-gated LWW page) typically have a
# few hundred to ~2000. Override with BIOLIT_JS_SHELL_CHAR_THRESHOLD.
_DEFAULT_JS_SHELL_THRESHOLD = 2000

# Status labels returned by classify_html_status.
STATUS_FULLTEXT = "fulltext"
STATUS_BOT_BLOCKED = "bot_blocked"
STATUS_JS_SHELL = "js_shell"
STATUS_ABSTRACT = "abstract"


# Cloudflare (and similar) bot-challenge markers. Deliberately specific so they
# appear only in challenge pages, never in real article HTML (an article *about*
# Cloudflare would not embed these exact tokens in its body).
_BOT_CHALLENGE_MARKERS = (
    re.compile(r"challenges\.cloudflare\.com", re.IGNORECASE),
    re.compile(r"cdn-cgi/challenge-platform", re.IGNORECASE),
    re.compile(r"__cf_chl_", re.IGNORECASE),
    re.compile(r"cf-browser-verification", re.IGNORECASE),
    re.compile(r"cf-chl-widget", re.IGNORECASE),
    re.compile(r"<title>\s*Just a moment", re.IGNORECASE),
    re.compile(r"<title>\s*Attention Required", re.IGNORECASE),
)

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[\s\S]*?</\1>", re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _js_shell_threshold(threshold: int | None = None) -> int:
    if threshold is not None:
        return threshold
    raw = os.environ.get("BIOLIT_JS_SHELL_CHAR_THRESHOLD")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return _DEFAULT_JS_SHELL_THRESHOLD


def is_bot_challenge(html: str | None) -> bool:
    """Return True if *html* looks like a Cloudflare / bot-challenge page.

    These are what an automated agent receives when a publisher fingerprints
    the request as a bot. Snapshotting one yields a captcha, not full text.
    """
    if not html:
        return False
    return any(marker.search(html) for marker in _BOT_CHALLENGE_MARKERS)


def visible_char_count(html: str | None) -> int:
    """Count visible characters: strip scripts/styles/comments/tags, collapse
    whitespace, and measure what is left. Mirrors the plugin's text-length
    check without parsing the DOM."""
    if not html:
        return 0
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _COMMENT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ")
    text = _WS_RE.sub(" ", text).strip()
    return len(text)


def has_substantial_content(html: str | None, threshold: int | None = None) -> bool:
    """Return True if *html* has enough visible text to be real article
    content rather than a JS-rendered shell.

    *threshold* (visible-char floor) defaults to
    ``BIOLIT_JS_SHELL_CHAR_THRESHOLD`` if set, else 2000.
    """
    return visible_char_count(html) >= _js_shell_threshold(threshold)


# Abstract-only URL shapes. PubMed is abstract-only by design; publishers
# redirect logged-out users from a full-text path to an /abstract/ stub.
_ABSTRACT_URL_PATTERNS = (
    re.compile(r"\bpubmed\.ncbi\.nlm\.nih\.gov\b", re.IGNORECASE),  # whole site is abstracts
    re.compile(r"/abstract/\d", re.IGNORECASE),                     # LWW / Wolters Kluwer
    re.compile(r"/article-abstract/", re.IGNORECASE),               # Oxford Academic (logged out)
)


def is_abstract_only_url(url: str | None) -> bool:
    """Return True if *url* is an abstract-only / preview landing page by its
    shape alone (PubMed, ``/abstract/<n>``, ``/article-abstract/``)."""
    if not url:
        return False
    return any(pat.search(url) for pat in _ABSTRACT_URL_PATTERNS)


def classify_html_status(
    html: str | None,
    url: str | None = None,
    *,
    threshold: int | None = None,
) -> str:
    """Classify a fetched landing page into one NON-fulltext status label.

    Returns one of :data:`STATUS_BOT_BLOCKED`, :data:`STATUS_ABSTRACT`,
    :data:`STATUS_JS_SHELL`, or :data:`STATUS_FULLTEXT` (when the page has
    substantial server-rendered content). Checked in order of specificity:
    a bot challenge wins over everything, then an abstract-only URL, then a
    thin JS shell, else the page looks like real full text.
    """
    if is_bot_challenge(html):
        return STATUS_BOT_BLOCKED
    if is_abstract_only_url(url):
        return STATUS_ABSTRACT
    if not has_substantial_content(html, threshold):
        return STATUS_JS_SHELL
    return STATUS_FULLTEXT

"""User-configurable PDF resolvers (Zotero "custom resolvers" analogue).

Zotero lets users add their own file resolvers — most commonly an
institutional OpenURL endpoint or a library EZproxy URL pattern — so
that "Find Full Text" can reach content the user is *authorized* to
access through their institution. This fetcher is the same seam for
biolit.

It is driven entirely by user configuration: a list of resolver entries,
each carrying a ``url_template`` with ``{doi}`` / ``{url}`` placeholders.
For every entry, the template is filled in, the resulting URL is fetched
with the user's own configured headers, and the response is returned if
it is a real PDF (``%PDF`` magic). When the resolved URL is an OpenURL /
proxy *landing page* rather than a direct PDF, set ``"scrape": true`` on
the entry to run the HTML through biolit's landing-page scraper.

Configuration sources (first non-empty wins):
  * the ``resolvers`` argument (a list of dicts), or
  * the ``BIOLIT_CUSTOM_RESOLVERS`` environment variable holding a JSON
    array of the same shape.

With neither configured this is a no-op returning None, so it can sit in
the fallback chain unconditionally.

Resolver entry schema (only ``url_template`` is required)::

    {
      "url_template": "https://proxy.lib.example.edu/login?url=https://doi.org/{doi}",
      "user_agent": "Mozilla/5.0 ...",   # optional UA override
      "headers": {"Cookie": "..."},      # optional extra request headers
      "scrape": true                       # optional: scrape HTML for a PDF link
    }

Supported placeholders: ``{doi}``, ``{doi_encoded}`` (URL-quoted),
``{url}``, ``{url_encoded}``.

IMPORTANT — this follows *user-configured* URL patterns using the user's
*own authorized* access. biolit never hardcodes, requests, or stores any
credentials; supply access (proxy login, cookies, etc.) yourself via the
template and ``headers``. You are responsible for ensuring your use
complies with your institution's and the publisher's terms.

Source label: ``custom_resolver_pdf``.
"""
import json
import os
from urllib.parse import quote

import requests

from biolit.fetchers.landing_page import fetch_via_landing_page

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _load_resolvers(resolvers: list | None) -> list[dict]:
    """Return the configured resolver list (arg first, else env JSON)."""
    if resolvers:
        return [r for r in resolvers if isinstance(r, dict)]
    raw = os.environ.get("BIOLIT_CUSTOM_RESOLVERS")
    if not raw or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    return [r for r in parsed if isinstance(r, dict)]


def _fill_template(template: str, doi: str | None, url: str | None) -> str | None:
    """Substitute the supported placeholders into *template*.

    Returns None when the template needs a value that is unavailable
    (e.g. ``{doi}`` but no DOI was provided), so we don't fire a request
    with a literal ``{doi}`` in it.
    """
    needs_doi = "{doi}" in template or "{doi_encoded}" in template
    needs_url = "{url}" in template or "{url_encoded}" in template
    if needs_doi and not doi:
        return None
    if needs_url and not url:
        return None
    return (
        template
        .replace("{doi_encoded}", quote(doi or "", safe=""))
        .replace("{doi}", doi or "")
        .replace("{url_encoded}", quote(url or "", safe=""))
        .replace("{url}", url or "")
    )


def fetch_via_custom_resolvers(
    doi: str | None = None,
    url: str | None = None,
    *,
    resolvers: list | None = None,
) -> bytes | None:
    """Try each user-configured resolver template, returning the first PDF.

    *resolvers* is a list of resolver-entry dicts (see the module
    docstring for the schema); when omitted it is read from the
    ``BIOLIT_CUSTOM_RESOLVERS`` environment variable (JSON). With neither
    configured this is a no-op returning None.

    Returns raw PDF bytes (verified to start with ``%PDF``), or None if no
    resolver yields a PDF or any error occurs. Never raises.
    """
    entries = _load_resolvers(resolvers)
    if not entries:
        return None

    for entry in entries:
        template = entry.get("url_template")
        if not template or not isinstance(template, str):
            continue
        resolved = _fill_template(template, doi, url)
        if not resolved:
            continue

        headers = {"User-Agent": entry.get("user_agent") or _DEFAULT_USER_AGENT}
        extra = entry.get("headers")
        if isinstance(extra, dict):
            headers.update({str(k): str(v) for k, v in extra.items()})

        try:
            resp = requests.get(resolved, headers=headers, timeout=60, allow_redirects=True)
            resp.raise_for_status()
        except Exception:
            continue

        content = resp.content or b""
        if content[:4] == b"%PDF":
            return content

        # OpenURL / proxy landing page → optionally scrape for a PDF link.
        if entry.get("scrape"):
            try:
                final_url = getattr(resp, "url", None) or resolved
                scraped = fetch_via_landing_page(
                    url=final_url, user_agent=headers["User-Agent"]
                )
                if scraped:
                    return scraped
            except Exception:
                continue
    return None

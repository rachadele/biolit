"""Fetcher sub-package."""
from biolit.fetchers._hooks import (
    FetchContext,
    FetchResult,
    Fetcher,
    register_fetcher,
    registered_fetchers,
    run_registered_fetchers,
)
from biolit.fetchers.preprints import fetch_preprint
from biolit.fetchers.pubmed import fetch_pmc_fulltext, fetch_pubmed_metadata
from biolit.fetchers.unpaywall import fetch_via_unpaywall

# Auto-register opt-in fetchers configured via environment variables.
# Each ``maybe_autoload`` returns True iff the corresponding env config was
# present and the fetcher was registered. Failures inside the autoloader
# are surfaced to stderr but never raised — biolit must still import even
# when an optional fetcher is misconfigured.
try:
    from biolit.fetchers import local_pdf as _local_pdf
    _local_pdf.maybe_autoload()
except Exception as _exc:  # pragma: no cover - defensive
    import sys as _sys
    print(f"[biolit] local_pdf autoload skipped: {_exc}", file=_sys.stderr)

try:
    from biolit.fetchers import zotero as _zotero
    _zotero.maybe_autoload()
except Exception as _exc:  # pragma: no cover - defensive
    import sys as _sys
    print(f"[biolit] zotero autoload skipped: {_exc}", file=_sys.stderr)


__all__ = [
    "FetchContext",
    "FetchResult",
    "Fetcher",
    "register_fetcher",
    "registered_fetchers",
    "run_registered_fetchers",
    "fetch_pubmed_metadata",
    "fetch_pmc_fulltext",
    "fetch_preprint",
    "fetch_via_unpaywall",
]

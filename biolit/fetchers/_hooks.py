"""Extension point for adding custom full-text fetchers.

By default biolit walks a fixed chain of fetchers: PMC → Europe PMC →
preprint XML → Unpaywall → Semantic Scholar → abstract. Some users have
additional sources of full text — a Zotero library with locally-stored
PDFs, a flat directory of papers on disk, an internal full-text database,
etc. — and want biolit to consult those first.

This module provides a tiny registry. A custom fetcher is any callable
that takes a :class:`FetchContext` and returns either a
:class:`FetchResult` (when it found something) or ``None`` (when it
didn't). Register it via :func:`register_fetcher`, optionally with a
priority controlling order:

    from biolit.fetchers import FetchContext, FetchResult, register_fetcher

    def my_fetcher(ctx: FetchContext) -> FetchResult | None:
        pmid = ctx.paper.get("pmid")
        if not pmid:
            return None
        ... # try to fetch text, return FetchResult or None

    register_fetcher(my_fetcher, priority=10.0)

The lower the priority, the earlier the fetcher is tried. Built-in
fetchers run *after* the registered ones — i.e. registered fetchers act
as a prepended chain. A registered fetcher that returns a non-empty
``FetchResult.text`` short-circuits the rest of the chain.

Reference implementations are in :mod:`biolit.fetchers.zotero` and
:mod:`biolit.fetchers.local_pdf`; both auto-register when the relevant
environment variables are set.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = [
    "FetchContext",
    "FetchResult",
    "Fetcher",
    "register_fetcher",
    "registered_fetchers",
    "run_registered_fetchers",
]


@dataclass
class FetchContext:
    """Per-call context passed to every registered fetcher.

    ``paper`` is the normalized record dict produced by ``fetch_record``;
    callers should treat it as read-only and look up identifiers via
    ``paper.get("pmid")``, ``paper.get("doi")``, ``paper.get("geo_accession")``.
    """

    paper: dict
    unpaywall_email: str | None = None
    sections_wanted: list[str] | None = None
    max_tokens: int = 0  # 0 means "use the pipeline default"


@dataclass
class FetchResult:
    """A successful full-text fetch.

    * ``text`` — selected sections, ready to send to the model. When this
      is empty, the registry treats the fetch as a miss.
    * ``source`` — short label written into the paper's ``text_source``
      field and persisted in artifact metadata. Examples: ``pmc_fulltext``,
      ``zotero_pdf``, ``local_pdf``.
    * ``artifacts`` — raw bytes that the pipeline will persist into the
      per-paper artifacts directory for reproducibility (e.g. the parsed
      PDF, the JATS XML).
    """

    text: str
    source: str
    artifacts: dict[str, bytes] = field(default_factory=dict)


Fetcher = Callable[[FetchContext], "FetchResult | None"]


_REGISTRY: list[tuple[float, str, Fetcher]] = []


def register_fetcher(fn: Fetcher, priority: float = 50.0, name: str | None = None) -> None:
    """Add ``fn`` to the registry.

    ``priority`` controls order — lower runs earlier. ``name`` is used in
    log messages; defaults to the callable's ``__name__``.
    """
    label = name or getattr(fn, "__name__", repr(fn))
    _REGISTRY.append((priority, label, fn))
    _REGISTRY.sort(key=lambda item: item[0])


def registered_fetchers() -> list[tuple[float, str, Fetcher]]:
    """Return ``(priority, name, callable)`` triples in execution order."""
    return list(_REGISTRY)


def run_registered_fetchers(ctx: FetchContext) -> FetchResult | None:
    """Walk the registry in priority order. Return the first non-empty result.

    Errors raised by individual fetchers are logged to stderr and the next
    fetcher is tried — a single broken fetcher must not break the chain.
    """
    for priority, label, fn in _REGISTRY:
        del priority  # only used for sorting
        try:
            result = fn(ctx)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"  [fetcher {label}] error: {exc}", file=sys.stderr)
            continue
        if result is None:
            continue
        if result.text:
            return result
        # Empty-text result is treated as a miss but artifacts are kept.
    return None

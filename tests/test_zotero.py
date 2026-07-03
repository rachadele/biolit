"""Tests for the Zotero fetcher's circuit-breaker.

Regression guard for the 2026-07 incident: an unreachable ``api.zotero.org``
blocked every request for ``timeout`` seconds with no cap, so a 400-study
batch spent ~2h retrying one dead endpoint. The breaker disables the fetcher
after a run of consecutive network failures so it falls through to the next
fetcher instead of wedging the whole run.
"""

from __future__ import annotations

import requests

from biolit.fetchers._hooks import FetchContext
from biolit.fetchers.zotero import ZoteroFetcher, ZoteroUnavailable


def _timeout_getter():
    """A ``requests.get`` replacement that always times out, counting hits."""
    calls = {"n": 0}

    def _get(*args, **kwargs):
        calls["n"] += 1
        raise requests.exceptions.Timeout("simulated")

    return _get, calls


def test_breaker_trips_after_consecutive_timeouts(monkeypatch):
    getter, calls = _timeout_getter()
    monkeypatch.setattr(requests, "get", getter)

    z = ZoteroFetcher(api_key="x", user_id="1", max_consecutive_timeouts=3)
    ctx = FetchContext(paper={"doi": "10.1/abc", "pmid": "999"})

    # Each __call__ -> _find_item tries DOI then PMID (2 requests/paper),
    # so the breaker trips partway through the second paper.
    for _ in range(5):
        assert z(ctx) is None  # never raises to the caller; always a clean miss
    assert z._disabled is True

    # Once tripped, further papers must not hit the network at all.
    hits_after_trip = calls["n"]
    assert z(ctx) is None
    assert calls["n"] == hits_after_trip  # no new requests.get calls


def test_breaker_short_circuits_direct_requests(monkeypatch):
    getter, _ = _timeout_getter()
    monkeypatch.setattr(requests, "get", getter)

    z = ZoteroFetcher(api_key="x", user_id="1", max_consecutive_timeouts=1)
    ctx = FetchContext(paper={"doi": "10.1/abc"})
    z(ctx)  # one DOI request -> one timeout -> trips (threshold 1)
    assert z._disabled is True

    # A direct call now raises without touching the network.
    hit_before = getter  # sentinel; count via a fresh probe
    try:
        z._request("https://api.zotero.org/probe")
    except ZoteroUnavailable:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("_request should raise ZoteroUnavailable when disabled")


def test_success_resets_the_streak(monkeypatch):
    """A successful call must clear the consecutive-timeout counter so
    intermittent blips never accumulate to a false trip."""
    state = {"mode": "timeout"}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return []

    def _get(*args, **kwargs):
        if state["mode"] == "timeout":
            raise requests.exceptions.Timeout("simulated")
        return _Resp()

    monkeypatch.setattr(requests, "get", _get)

    z = ZoteroFetcher(api_key="x", user_id="1", max_consecutive_timeouts=3)

    # Two timeouts, then a success: streak must reset to 0, breaker stays closed.
    for _ in range(2):
        try:
            z._search_by_query("q")
        except requests.exceptions.Timeout:
            pass
    assert z._consecutive_timeouts == 2
    state["mode"] = "ok"
    z._search_by_query("q")  # success
    assert z._consecutive_timeouts == 0
    assert z._disabled is False

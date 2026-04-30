"""Tests for the fetcher-registry extension point."""

from __future__ import annotations

import pytest

from biolit.fetchers._hooks import (
    FetchContext,
    FetchResult,
    register_fetcher,
    registered_fetchers,
    run_registered_fetchers,
)
from biolit.fetchers import _hooks


@pytest.fixture(autouse=True)
def _clear_registry():
    """Each test gets a clean registry."""
    saved = _hooks._REGISTRY[:]
    _hooks._REGISTRY.clear()
    yield
    _hooks._REGISTRY.clear()
    _hooks._REGISTRY.extend(saved)


def test_register_orders_by_priority():
    register_fetcher(lambda ctx: None, priority=20.0, name="b")
    register_fetcher(lambda ctx: None, priority=10.0, name="a")
    register_fetcher(lambda ctx: None, priority=30.0, name="c")
    names = [name for _, name, _ in registered_fetchers()]
    assert names == ["a", "b", "c"]


def test_run_returns_first_text_result():
    def first(ctx):
        return None

    def second(ctx):
        return FetchResult(text="hello", source="second")

    def third(ctx):
        return FetchResult(text="should-not-reach", source="third")

    register_fetcher(first, priority=10, name="first")
    register_fetcher(second, priority=20, name="second")
    register_fetcher(third, priority=30, name="third")

    result = run_registered_fetchers(FetchContext(paper={"pmid": "1"}))
    assert result is not None
    assert result.source == "second"
    assert result.text == "hello"


def test_empty_text_result_is_treated_as_miss():
    def empty(ctx):
        return FetchResult(text="", source="empty", artifacts={"raw": b"data"})

    register_fetcher(empty, priority=10, name="empty")
    result = run_registered_fetchers(FetchContext(paper={}))
    assert result is None


def test_exception_in_fetcher_is_logged_not_raised(capsys):
    def boom(ctx):
        raise RuntimeError("boom")

    def ok(ctx):
        return FetchResult(text="ok", source="ok")

    register_fetcher(boom, priority=10, name="boom")
    register_fetcher(ok, priority=20, name="ok")

    result = run_registered_fetchers(FetchContext(paper={}))
    assert result is not None and result.source == "ok"
    err = capsys.readouterr().err
    assert "boom" in err


def test_no_registered_fetchers_returns_none():
    assert run_registered_fetchers(FetchContext(paper={})) is None


def test_local_pdf_finds_via_index(tmp_path):
    """Lookup is by DOI from a pre-built JSON index — filename is irrelevant."""
    import json

    from biolit.fetchers.local_pdf import LocalPDFFetcher

    pdf_dir = tmp_path / "papers"
    pdf_dir.mkdir()
    nested = pdf_dir / "Zotero-style" / "ABC123"
    nested.mkdir(parents=True)
    target = nested / "Document.pdf"
    target.write_bytes(b"%PDF-1.4 fake")

    index_path = tmp_path / "idx.json"
    index_path.write_text(json.dumps({
        "directory": str(pdf_dir),
        "n_pdfs": 1,
        "n_indexed": 1,
        "n_unindexed": 0,
        "doi_to_path": {"10.1234/journal.abc.123": str(target)},
        "unindexed_sample": [],
    }))

    fetcher = LocalPDFFetcher(directory=pdf_dir, index_path=index_path)
    assert fetcher._find({"doi": "10.1234/journal.abc.123"}) == target
    # Case-insensitive lookup
    assert fetcher._find({"doi": "10.1234/JOURNAL.ABC.123"}) == target


def test_local_pdf_misses_without_index(tmp_path, capsys):
    """No index → fetcher logs a hint and returns None (not an error)."""
    from biolit.fetchers.local_pdf import LocalPDFFetcher

    pdf_dir = tmp_path / "papers"
    pdf_dir.mkdir()
    index_path = tmp_path / "missing-idx.json"

    fetcher = LocalPDFFetcher(directory=pdf_dir, index_path=index_path)
    assert fetcher._find({"doi": "10.1234/abc"}) is None
    err = capsys.readouterr().err
    assert "no index" in err and "biolit.fetchers.local_pdf" in err


def test_local_pdf_misses_unknown_doi(tmp_path):
    import json

    from biolit.fetchers.local_pdf import LocalPDFFetcher

    pdf_dir = tmp_path / "papers"
    pdf_dir.mkdir()
    index_path = tmp_path / "idx.json"
    index_path.write_text(json.dumps({"doi_to_path": {"10.1/known": str(pdf_dir / "x.pdf")}}))
    fetcher = LocalPDFFetcher(directory=pdf_dir, index_path=index_path)
    assert fetcher._find({"doi": "10.9999/unknown"}) is None


def test_doi_regex_finds_doi_in_text():
    from biolit.fetchers.local_pdf import DOI_RE, _clean_doi

    text = "...this paper is published as DOI: 10.1038/s41586-024-12345-6, accessed via..."
    m = DOI_RE.search(text)
    assert m is not None
    assert _clean_doi(m.group(0)) == "10.1038/s41586-024-12345-6"


def test_doi_regex_strips_trailing_punctuation():
    from biolit.fetchers.local_pdf import DOI_RE, _clean_doi

    m = DOI_RE.search("see 10.1234/abc.def.")
    assert m is not None
    assert _clean_doi(m.group(0)) == "10.1234/abc.def"


def _mock_response(status: int, json_body=None, content: bytes = b""):
    """Build a minimal stand-in for ``requests.Response``."""
    from unittest.mock import MagicMock

    r = MagicMock()
    r.status_code = status
    r.content = content
    if json_body is not None:
        r.json.return_value = json_body
    r.raise_for_status = lambda: None
    return r


def test_zotero_finds_via_attachment_parent(monkeypatch):
    """qmode=everything returns attachment hits; fetcher must follow parentItem."""
    from biolit.fetchers.zotero import ZoteroFetcher

    z = ZoteroFetcher(api_key="k", user_id="123")
    target_doi = "10.1234/test.paper"

    def fake_get(url, **kwargs):
        if kwargs.get("params", {}).get("q"):
            # _search_by_query: return one attachment hit, no DOI on attachment
            return _mock_response(200, json_body=[{
                "key": "ATTACH1",
                "data": {"itemType": "attachment", "parentItem": "PARENT1"},
            }])
        if url.endswith("/items/PARENT1"):
            return _mock_response(200, json_body={
                "key": "PARENT1",
                "data": {"itemType": "journalArticle", "DOI": target_doi.upper(), "title": "x"},
            })
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr("biolit.fetchers.zotero.requests.get", fake_get)
    result = z._find_item({"doi": target_doi, "pmid": None})
    assert result is not None
    assert result["key"] == "PARENT1"


def test_zotero_does_not_return_unrelated_attachment_hits(monkeypatch):
    """Single attachment hit whose parent has a DIFFERENT DOI must NOT be returned
    (was the "exactly one hit" soft-fallback bug)."""
    from biolit.fetchers.zotero import ZoteroFetcher

    z = ZoteroFetcher(api_key="k", user_id="123")

    def fake_get(url, **kwargs):
        if kwargs.get("params", {}).get("q"):
            return _mock_response(200, json_body=[{
                "key": "ATTACH1",
                "data": {"itemType": "attachment", "parentItem": "OTHER"},
            }])
        if url.endswith("/items/OTHER"):
            return _mock_response(200, json_body={
                "key": "OTHER",
                "data": {"itemType": "journalArticle", "DOI": "10.9999/different"},
            })
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr("biolit.fetchers.zotero.requests.get", fake_get)
    assert z._find_item({"doi": "10.1234/we-want-this", "pmid": None}) is None


def test_zotero_download_falls_back_to_local_storage(monkeypatch, tmp_path):
    """When /file returns 404, read the PDF from local Zotero storage."""
    from biolit.fetchers.zotero import ZoteroFetcher

    z = ZoteroFetcher(api_key="k", user_id="123")
    monkeypatch.setenv("ZOTERO_DATA_DIR", str(tmp_path))

    fname = "Author 2020 - Paper.pdf"
    pdf_dir = tmp_path / "storage" / "ATTACH1"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / fname).write_bytes(b"%PDF-1.4 fake content")

    monkeypatch.setattr(
        "biolit.fetchers.zotero.requests.get",
        lambda *a, **kw: _mock_response(404, content=b"Not found"),
    )

    result = z._download_attachment("ATTACH1", filename=fname)
    assert result == b"%PDF-1.4 fake content"

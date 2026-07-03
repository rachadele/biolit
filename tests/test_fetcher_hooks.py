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


def test_zotero_finds_preprint_when_q_search_misses(monkeypatch):
    """Preprint items are routinely missed by q=<DOI>&qmode=everything
    (Zotero hasn't full-text indexed the local PDF). The fetcher must
    fall back to listing itemType=preprint and matching DOIs directly.
    """
    from biolit.fetchers.zotero import ZoteroFetcher

    z = ZoteroFetcher(api_key="k", user_id="123")
    target_doi = "10.1101/2025.03.17.25324098"

    calls = {"q_search": 0, "preprint_list": 0}

    def fake_get(url, **kwargs):
        params = kwargs.get("params") or {}
        if "q" in params:
            calls["q_search"] += 1
            return _mock_response(200, json_body=[])
        if params.get("itemType") == "preprint":
            calls["preprint_list"] += 1
            return _mock_response(200, json_body=[
                {
                    "key": "OTHER_PREPRINT",
                    "data": {"itemType": "preprint", "DOI": "10.1101/different"},
                },
                {
                    "key": "4UA6SYH7",
                    "data": {"itemType": "preprint", "DOI": target_doi},
                },
            ])
        raise AssertionError(f"unexpected request: {url} {params}")

    monkeypatch.setattr("biolit.fetchers.zotero.requests.get", fake_get)
    result = z._find_item({"doi": target_doi, "pmid": None})
    assert result is not None
    assert result["key"] == "4UA6SYH7"
    assert calls["q_search"] >= 1
    assert calls["preprint_list"] == 1


def test_zotero_preprint_fallback_skips_when_no_identifiers(monkeypatch):
    """If neither DOI nor PMID is set, don't bother listing preprints."""
    from biolit.fetchers.zotero import ZoteroFetcher

    z = ZoteroFetcher(api_key="k", user_id="123")

    def fake_get(url, **kwargs):
        raise AssertionError(f"no API call expected; got {url}")

    monkeypatch.setattr("biolit.fetchers.zotero.requests.get", fake_get)
    assert z._find_item({"doi": None, "pmid": None}) is None


def test_zotero_preprint_fallback_returns_none_when_no_match(monkeypatch):
    """Preprint list exists but none match — return None, don't crash."""
    from biolit.fetchers.zotero import ZoteroFetcher

    z = ZoteroFetcher(api_key="k", user_id="123")

    def fake_get(url, **kwargs):
        params = kwargs.get("params") or {}
        if "q" in params:
            return _mock_response(200, json_body=[])
        if params.get("itemType") == "preprint":
            return _mock_response(200, json_body=[
                {"key": "X", "data": {"itemType": "preprint", "DOI": "10.1/other"}},
            ])
        raise AssertionError(f"unexpected request: {url} {params}")

    monkeypatch.setattr("biolit.fetchers.zotero.requests.get", fake_get)
    assert z._find_item({"doi": "10.1/missing", "pmid": None}) is None


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


# ---------------------------------------------------------------------------
# BibTeX fetcher
# ---------------------------------------------------------------------------


def _write_pdf(path):
    """Write a minimal placeholder PDF and return ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 fake")
    return path


def test_bibtex_parses_doi_pmid_file_and_citekey(tmp_path):
    from biolit.fetchers.bibtex import parse_bibtex

    pdf = _write_pdf(tmp_path / "storage" / "ABC" / "Doe 2024.pdf")
    bib = tmp_path / "library.bib"
    bib.write_text(f"""\
@article{{doe2024,
  title = {{An example paper}},
  doi = {{10.1234/Example.2024}},
  pmid = {{12345678}},
  file = {{{pdf}}}
}}
""")
    idx = parse_bibtex(bib.read_text())
    # DOI is lowercased in the lookup table.
    assert idx.by_doi == {"10.1234/example.2024": str(pdf)}
    assert idx.by_pmid == {"12345678": str(pdf)}
    assert idx.by_citekey == {"doe2024": str(pdf)}
    assert idx.n_entries == 1
    assert idx.n_with_pdf == 1


def test_bibtex_picks_first_pdf_from_semicolon_list(tmp_path):
    """BBT exports ``file = {pdf;html;...}``; the .pdf wins."""
    from biolit.fetchers.bibtex import parse_bibtex

    pdf = _write_pdf(tmp_path / "x.pdf")
    bib_text = f"""\
@article{{russell2025,
  doi = {{10.18653/v1/2025.acl-long.267}},
  file = {{{pdf};{tmp_path}/note.html}}
}}
"""
    idx = parse_bibtex(bib_text)
    assert idx.by_doi["10.18653/v1/2025.acl-long.267"] == str(pdf)


def test_bibtex_handles_jabref_triple_format(tmp_path):
    """Classic JabRef ``description:path:type`` form."""
    from biolit.fetchers.bibtex import parse_bibtex

    pdf = _write_pdf(tmp_path / "papers" / "smith.pdf")
    bib_text = f"""\
@article{{smith2010,
  doi = {{10.1/smith}},
  file = {{Smith 2010:{pdf}:PDF}}
}}
"""
    idx = parse_bibtex(bib_text)
    assert idx.by_doi["10.1/smith"] == str(pdf)


def test_bibtex_skips_entries_without_pdf(tmp_path):
    from biolit.fetchers.bibtex import parse_bibtex

    bib_text = """\
@article{nopdf2020,
  doi = {10.1/nopdf},
  title = {No file field here}
}
"""
    idx = parse_bibtex(bib_text)
    assert idx.n_entries == 1
    assert idx.n_with_pdf == 0
    assert idx.by_doi == {}
    assert idx.by_citekey == {}


def test_bibtex_fetcher_finds_by_doi_pmid_citekey(tmp_path):
    from biolit.fetchers.bibtex import BibTeXFetcher

    pdf = _write_pdf(tmp_path / "p.pdf")
    bib = tmp_path / "library.bib"
    bib.write_text(f"""\
@article{{doe2024,
  doi = {{10.1/abc}},
  pmid = {{99999}},
  file = {{{pdf}}}
}}
""")
    f = BibTeXFetcher(bib_path=bib)
    assert f._find({"doi": "10.1/abc"}) == pdf
    # Case-insensitive DOI match.
    assert f._find({"doi": "10.1/ABC"}) == pdf
    assert f._find({"pmid": "99999"}) == pdf
    assert f._find({"citekey": "doe2024"}) == pdf
    # Unknown identifiers miss.
    assert f._find({"doi": "10.9/nope"}) is None
    assert f._find({"pmid": "11111"}) is None
    assert f._find({"citekey": "smith2099"}) is None


def test_bibtex_fetcher_returns_none_when_pdf_missing(tmp_path, capsys):
    """Stale ``file =`` paths log a warning and miss without crashing."""
    from biolit.fetchers.bibtex import BibTeXFetcher

    bib = tmp_path / "library.bib"
    bib.write_text("""\
@article{ghost2030,
  doi = {10.1/ghost},
  file = {/nonexistent/path/to/ghost.pdf}
}
""")
    f = BibTeXFetcher(bib_path=bib)
    assert f._find({"doi": "10.1/ghost"}) is None
    err = capsys.readouterr().err
    assert "stale" in err and "ghost.pdf" in err


def test_bibtex_fetcher_reparses_when_file_mtime_changes(tmp_path):
    """Editing the .bib file invalidates the cached index automatically."""
    import os as _os
    from biolit.fetchers.bibtex import BibTeXFetcher

    pdf1 = _write_pdf(tmp_path / "v1.pdf")
    pdf2 = _write_pdf(tmp_path / "v2.pdf")
    bib = tmp_path / "library.bib"
    bib.write_text(f"@article{{a, doi = {{10.1/a}}, file = {{{pdf1}}}}}\n")
    f = BibTeXFetcher(bib_path=bib)
    assert f._find({"doi": "10.1/a"}) == pdf1

    # Rewrite to point at pdf2; bump mtime forward to ensure cache invalidates.
    bib.write_text(f"@article{{a, doi = {{10.1/a}}, file = {{{pdf2}}}}}\n")
    future = bib.stat().st_mtime + 5
    _os.utime(bib, (future, future))
    assert f._find({"doi": "10.1/a"}) == pdf2


def test_bibtex_fetcher_handles_missing_bib_file(tmp_path, capsys):
    from biolit.fetchers.bibtex import BibTeXFetcher

    f = BibTeXFetcher(bib_path=tmp_path / "missing.bib")
    assert f._find({"doi": "10.1/anything"}) is None
    err = capsys.readouterr().err
    assert "does not exist" in err


def test_zotero_maybe_autoload_falls_back_to_keychain(monkeypatch):
    """When env vars are unset, credentials come from the keychain helper."""
    from biolit.fetchers import zotero as _zot

    monkeypatch.delenv("ZOTERO_API_KEY", raising=False)
    monkeypatch.delenv("ZOTERO_USER_ID", raising=False)
    monkeypatch.delenv("ZOTERO_GROUP_ID", raising=False)

    keychain = {"ZOTERO_API_KEY": "kc-key", "ZOTERO_USER_ID": "kc-user"}
    monkeypatch.setattr(
        "biolit.fetchers.zotero.resolve_api_key",
        lambda name: keychain.get(name),
    )

    assert _zot.maybe_autoload() is True
    fetchers = {name: fn for _, name, fn in registered_fetchers()}
    assert "zotero" in fetchers
    assert fetchers["zotero"].api_key == "kc-key"
    assert fetchers["zotero"].user_id == "kc-user"


def test_zotero_maybe_autoload_skips_when_neither_env_nor_keychain(monkeypatch):
    from biolit.fetchers import zotero as _zot

    monkeypatch.delenv("ZOTERO_API_KEY", raising=False)
    monkeypatch.delenv("ZOTERO_USER_ID", raising=False)
    monkeypatch.delenv("ZOTERO_GROUP_ID", raising=False)
    monkeypatch.setattr("biolit.fetchers.zotero.resolve_api_key", lambda name: None)

    assert _zot.maybe_autoload() is False
    names = [name for _, name, _ in registered_fetchers()]
    assert "zotero" not in names


def test_bibtex_maybe_autoload_registers_when_env_set(tmp_path, monkeypatch):
    from biolit.fetchers import bibtex as _bib

    bib = tmp_path / "library.bib"
    bib.write_text("@article{x, doi = {10.1/x}, file = {/tmp/x.pdf}}\n")
    monkeypatch.setenv("BIOLIT_BIBTEX", str(bib))

    assert _bib.maybe_autoload() is True
    names = [name for _, name, _ in registered_fetchers()]
    assert "bibtex" in names


def test_bibtex_maybe_autoload_skips_when_path_invalid(tmp_path, monkeypatch, capsys):
    from biolit.fetchers import bibtex as _bib

    monkeypatch.setenv("BIOLIT_BIBTEX", str(tmp_path / "does-not-exist.bib"))
    assert _bib.maybe_autoload() is False
    err = capsys.readouterr().err
    assert "is not a file" in err


# ---------------------------------------------------------------------------
# local_pdf incremental indexing
# ---------------------------------------------------------------------------


def test_local_pdf_incremental_reuses_unchanged_files(tmp_path, monkeypatch):
    """A second build reuses prior DOIs without re-running pdfminer."""
    from biolit.fetchers import local_pdf

    pdf_dir = tmp_path / "papers"
    pdf_dir.mkdir()
    p1 = _write_pdf(pdf_dir / "a.pdf")
    p2 = _write_pdf(pdf_dir / "b.pdf")
    idx_path = tmp_path / "idx.json"

    extract_calls = {"n": 0}

    def fake_extract_doi(path):
        extract_calls["n"] += 1
        return "10.1/" + path.stem
    monkeypatch.setattr(local_pdf, "extract_doi", fake_extract_doi)

    # First build extracts both.
    local_pdf.build_index(pdf_dir, output_path=idx_path, verbose=False)
    assert extract_calls["n"] == 2

    # Second build (default incremental=True) should not extract again.
    local_pdf.build_index(pdf_dir, output_path=idx_path, verbose=False)
    assert extract_calls["n"] == 2  # unchanged

    # The index should still resolve both DOIs.
    payload = __import__("json").loads(idx_path.read_text())
    assert set(payload["doi_to_path"].keys()) == {"10.1/a", "10.1/b"}
    assert payload["schema_version"] == 2
    assert set(payload["entries"].keys()) == {str(p1), str(p2)}


def test_local_pdf_incremental_reextracts_changed_files(tmp_path, monkeypatch):
    """Bumping a file's mtime forces re-extraction of just that file."""
    import os as _os
    from biolit.fetchers import local_pdf

    pdf_dir = tmp_path / "papers"
    pdf_dir.mkdir()
    pa = _write_pdf(pdf_dir / "a.pdf")
    pb = _write_pdf(pdf_dir / "b.pdf")
    idx_path = tmp_path / "idx.json"

    state = {"a_doi": "10.1/old-a", "b_doi": "10.1/old-b"}
    extract_paths: list[str] = []

    def fake_extract_doi(path):
        extract_paths.append(str(path))
        return state["a_doi"] if path.name == "a.pdf" else state["b_doi"]
    monkeypatch.setattr(local_pdf, "extract_doi", fake_extract_doi)

    local_pdf.build_index(pdf_dir, output_path=idx_path, verbose=False)
    assert sorted(p for p in extract_paths if p.endswith(".pdf")) == sorted([str(pa), str(pb)])
    extract_paths.clear()

    # Modify only 'a.pdf' and bump its mtime.
    pa.write_bytes(b"%PDF-1.4 newer content")
    future = pa.stat().st_mtime + 5
    _os.utime(pa, (future, future))
    state["a_doi"] = "10.1/new-a"

    local_pdf.build_index(pdf_dir, output_path=idx_path, verbose=False)
    # Only 'a.pdf' should have been re-extracted.
    assert extract_paths == [str(pa)]

    payload = __import__("json").loads(idx_path.read_text())
    assert payload["doi_to_path"]["10.1/new-a"] == str(pa)
    assert payload["doi_to_path"]["10.1/old-b"] == str(pb)
    assert "10.1/old-a" not in payload["doi_to_path"]


def test_local_pdf_incremental_drops_deleted_files(tmp_path, monkeypatch):
    from biolit.fetchers import local_pdf

    pdf_dir = tmp_path / "papers"
    pdf_dir.mkdir()
    pa = _write_pdf(pdf_dir / "a.pdf")
    pb = _write_pdf(pdf_dir / "b.pdf")
    idx_path = tmp_path / "idx.json"

    monkeypatch.setattr(
        local_pdf, "extract_doi",
        lambda path: "10.1/" + path.stem,
    )
    local_pdf.build_index(pdf_dir, output_path=idx_path, verbose=False)

    pb.unlink()
    local_pdf.build_index(pdf_dir, output_path=idx_path, verbose=False)

    payload = __import__("json").loads(idx_path.read_text())
    assert set(payload["entries"].keys()) == {str(pa)}
    assert payload["doi_to_path"] == {"10.1/a": str(pa)}


def test_local_pdf_incremental_picks_up_new_files(tmp_path, monkeypatch):
    from biolit.fetchers import local_pdf

    pdf_dir = tmp_path / "papers"
    pdf_dir.mkdir()
    pa = _write_pdf(pdf_dir / "a.pdf")
    idx_path = tmp_path / "idx.json"

    monkeypatch.setattr(
        local_pdf, "extract_doi",
        lambda path: "10.1/" + path.stem,
    )
    local_pdf.build_index(pdf_dir, output_path=idx_path, verbose=False)

    pb = _write_pdf(pdf_dir / "b.pdf")
    local_pdf.build_index(pdf_dir, output_path=idx_path, verbose=False)

    payload = __import__("json").loads(idx_path.read_text())
    assert set(payload["entries"].keys()) == {str(pa), str(pb)}
    assert set(payload["doi_to_path"].keys()) == {"10.1/a", "10.1/b"}


def test_local_pdf_rebuild_forces_reextraction(tmp_path, monkeypatch):
    from biolit.fetchers import local_pdf

    pdf_dir = tmp_path / "papers"
    pdf_dir.mkdir()
    _write_pdf(pdf_dir / "a.pdf")
    idx_path = tmp_path / "idx.json"

    calls = {"n": 0}

    def fake_extract_doi(path):
        calls["n"] += 1
        return "10.1/a"
    monkeypatch.setattr(local_pdf, "extract_doi", fake_extract_doi)

    local_pdf.build_index(pdf_dir, output_path=idx_path, verbose=False)
    assert calls["n"] == 1

    # incremental=False forces re-extraction even though mtime is unchanged.
    local_pdf.build_index(pdf_dir, output_path=idx_path, verbose=False, incremental=False)
    assert calls["n"] == 2


def test_local_pdf_v1_index_is_rebuilt_under_incremental(tmp_path, monkeypatch):
    """Old indexes (no ``entries`` block) should trigger full re-extraction."""
    import json as _json
    from biolit.fetchers import local_pdf

    pdf_dir = tmp_path / "papers"
    pdf_dir.mkdir()
    pa = _write_pdf(pdf_dir / "a.pdf")
    idx_path = tmp_path / "idx.json"
    # v1 schema: no `entries` block, just doi_to_path.
    idx_path.write_text(_json.dumps({
        "directory": str(pdf_dir),
        "n_pdfs": 1,
        "n_indexed": 1,
        "n_unindexed": 0,
        "doi_to_path": {"10.1/legacy": str(pa)},
        "unindexed_sample": [],
    }))

    calls = {"n": 0}

    def fake_extract_doi(path):
        calls["n"] += 1
        return "10.1/a"
    monkeypatch.setattr(local_pdf, "extract_doi", fake_extract_doi)

    local_pdf.build_index(pdf_dir, output_path=idx_path, verbose=False)
    # v1 had no per-file state, so incremental rebuild must re-extract.
    assert calls["n"] == 1
    payload = _json.loads(idx_path.read_text())
    assert payload["schema_version"] == 2
    assert "entries" in payload


def test_local_pdf_atomic_write_does_not_leave_tmp(tmp_path, monkeypatch):
    from biolit.fetchers import local_pdf

    pdf_dir = tmp_path / "papers"
    pdf_dir.mkdir()
    _write_pdf(pdf_dir / "a.pdf")
    idx_path = tmp_path / "idx.json"

    monkeypatch.setattr(local_pdf, "extract_doi", lambda path: "10.1/a")
    local_pdf.build_index(pdf_dir, output_path=idx_path, verbose=False)

    assert idx_path.is_file()
    # No leftover .tmp from the atomic write.
    assert not (idx_path.parent / (idx_path.name + ".tmp")).exists()

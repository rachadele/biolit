"""Tests for the supplementary-materials fetcher (Europe PMC route)."""
import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from biolit.fetchers import supplementary as supp
from biolit.fetchers.supplementary import SuppFile, fetch_supplementary


# JATS declaring two supplementary files (href on a child <media>) plus
# one whose href omits the extension, to exercise stem matching.
NXML = b"""<?xml version="1.0"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <body><sec><title>Methods</title><p>see supplement</p></sec></body>
  <back>
    <sec sec-type="supplementary-material">
      <supplementary-material id="SM1">
        <label>Supplementary Methods</label>
        <media xlink:href="mmc1.pdf"/>
      </supplementary-material>
      <supplementary-material id="SM2">
        <label>Table S1</label>
        <media xlink:href="mmc2.xlsx"/>
      </supplementary-material>
      <supplementary-material id="SM3">
        <label>Supplementary Data</label>
        <media xlink:href="media-3"/>
      </supplementary-material>
    </sec>
  </back>
</article>
"""

LABELS = supp._supp_labels_from_nxml(NXML)


def _make_zip(members: dict[str, bytes]) -> bytes:
    """Build a real ZIP (PK magic) like the Europe PMC supplementaryFiles
    response."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


SUPP_ZIP = _make_zip({
    "mmc1.pdf": b"%PDF-1.4 methods",          # declared supp (pdf)
    "mmc2.xlsx": b"PK\x03\x04 xlsx-bytes",     # declared supp (other, no text)
    "media-3.pdf": b"%PDF-1.4 data",           # declared via ext-less href
    "Fig1_HTML.jpg": b"\xff\xd8\xff jpeg",     # loose figure -> excluded
})


# ---------------------------------------------------------------------------
# fetch_supplementary — zip parsing / filtering / labels
# ---------------------------------------------------------------------------

class TestFetchSupplementary:
    def _patch(self, pdf_text="METHODS: adult male C57BL/6 mice"):
        return (
            patch.object(supp, "_download_supp_zip", return_value=SUPP_ZIP),
            patch.object(supp, "_supp_labels", return_value=LABELS),
            patch("biolit.parsers.pdf.extract_pdf_text", return_value=pdf_text),
        )

    def test_returns_declared_and_text_files_excludes_loose_figures(self):
        a, b, c = self._patch()
        with a, b, c:
            files = {f.name: f for f in fetch_supplementary(pmcid="PMC1")}
        # declared supp (mmc1, mmc2, media-3) kept; loose Fig1 excluded
        assert set(files) == {"mmc1.pdf", "mmc2.xlsx", "media-3.pdf"}
        assert "Fig1_HTML.jpg" not in files

    def test_labels_kinds_and_text(self):
        a, b, c = self._patch()
        with a, b, c:
            files = {f.name: f for f in fetch_supplementary(pmcid="PMC1")}
        assert files["mmc1.pdf"].label == "Supplementary Methods"
        assert files["mmc1.pdf"].kind == "pdf"
        assert "C57BL/6" in files["mmc1.pdf"].text
        assert files["mmc2.xlsx"].label == "Table S1"
        assert files["mmc2.xlsx"].kind == "other"   # xlsx not text-extractable
        assert files["mmc2.xlsx"].text == ""
        assert files["media-3.pdf"].kind == "pdf"    # ext-less JATS href matched
        assert files["media-3.pdf"].label == "Supplementary Data"

    def test_text_extractable_kept_even_without_labels(self):
        # No JATS labels available -> still return the text-extractable files.
        a, _, c = self._patch()
        with a, patch.object(supp, "_supp_labels", return_value={}), c:
            files = {f.name: f for f in fetch_supplementary(pmcid="PMC1")}
        assert set(files) == {"mmc1.pdf", "media-3.pdf"}  # pdfs kept
        assert "mmc2.xlsx" not in files  # not text-extractable, not declared
        assert all(f.label == "" for f in files.values())

    def test_extract_false_lists_without_text(self):
        a, b, _ = self._patch()
        with a, b:
            files = fetch_supplementary(pmcid="PMC1", extract=False)
        assert {f.name for f in files} == {"mmc1.pdf", "mmc2.xlsx", "media-3.pdf"}
        assert all(f.text == "" for f in files)

    def test_no_pmcid_resolved_returns_empty(self):
        assert fetch_supplementary() == []

    def test_no_zip_returns_empty(self):
        with patch.object(supp, "_download_supp_zip", return_value=None):
            assert fetch_supplementary(pmcid="PMC1") == []

    def test_resolves_pmid_to_pmcid(self):
        a, b, c = self._patch()
        with patch.object(supp, "pmid_to_pmcid", return_value="PMC1") as m, a, b, c:
            files = fetch_supplementary(pmid="999")
        m.assert_called_once_with("999")
        assert files


# ---------------------------------------------------------------------------
# _resolve_pmcid
# ---------------------------------------------------------------------------

class TestResolvePmcid:
    def test_prefers_pmcid_and_normalises(self):
        assert supp._resolve_pmcid("PMC5", None, None) == "PMC5"
        assert supp._resolve_pmcid("5", None, None) == "PMC5"

    def test_falls_back_to_pmid_then_doi(self):
        with patch.object(supp, "pmid_to_pmcid", return_value="PMC7") as mp:
            assert supp._resolve_pmcid(None, "111", None) == "PMC7"
            mp.assert_called_once()
        with patch.object(supp, "doi_to_pmcid", return_value="PMC8") as md:
            assert supp._resolve_pmcid(None, None, "10.1/x") == "PMC8"
            md.assert_called_once()

    def test_none_when_no_ids(self):
        assert supp._resolve_pmcid(None, None, None) is None


# ---------------------------------------------------------------------------
# _download_supp_zip / _supp_labels — Europe PMC HTTP
# ---------------------------------------------------------------------------

def _resp(content=b"", status=200):
    r = MagicMock()
    r.content = content
    r.raise_for_status = MagicMock()
    if status >= 400:
        r.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return r


class TestDownloadSuppZip:
    @patch("biolit.fetchers.supplementary.requests.get")
    def test_returns_zip_bytes(self, mock_get):
        mock_get.return_value = _resp(SUPP_ZIP)
        out = supp._download_supp_zip("PMC1")
        assert out[:2] == b"PK"
        assert "PMC1/supplementaryFiles" in mock_get.call_args[0][0]

    @patch("biolit.fetchers.supplementary.requests.get")
    def test_rejects_non_zip(self, mock_get):
        mock_get.return_value = _resp(b"<html>not found</html>")
        assert supp._download_supp_zip("PMC1") is None

    @patch("biolit.fetchers.supplementary.requests.get")
    def test_none_on_error(self, mock_get):
        mock_get.side_effect = ConnectionError("down")
        assert supp._download_supp_zip("PMC1") is None


class TestSuppLabels:
    @patch("biolit.fetchers.supplementary.requests.get")
    def test_parses_labels_from_jats(self, mock_get):
        mock_get.return_value = _resp(NXML)
        labels = supp._supp_labels("PMC1")
        assert labels["mmc1"] == "Supplementary Methods"
        assert labels["media-3"] == "Supplementary Data"

    @patch("biolit.fetchers.supplementary.requests.get")
    def test_empty_on_error(self, mock_get):
        mock_get.side_effect = ConnectionError("down")
        assert supp._supp_labels("PMC1") == {}

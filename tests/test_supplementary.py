"""Tests for the supplementary-materials fetcher."""
import io
import tarfile
from unittest.mock import MagicMock, patch

import pytest

from biolit.fetchers import supplementary as supp
from biolit.fetchers.supplementary import SuppFile, fetch_supplementary


# JATS that declares two supplementary files (href on a child <media>) plus
# one whose href omits the extension, to exercise stem matching.
NXML = b"""<?xml version="1.0"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <front><article-meta><title-group><article-title>T</article-title></title-group></article-meta></front>
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


def _make_package(members: dict[str, bytes]) -> bytes:
    """Build a real .tar.gz (gzip magic + tar) like a PMC OA package."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


PACKAGE = _make_package({
    "PMC1/article.nxml": NXML,
    "PMC1/mmc1.pdf": b"%PDF-1.4 methods",          # declared supp (pdf)
    "PMC1/mmc2.xlsx": b"PK\x03\x04 xlsx-bytes",     # declared supp (other)
    "PMC1/media-3.pdf": b"%PDF-1.4 data",           # declared via ext-less href
    "PMC1/main-article.pdf": b"%PDF-1.4 main",      # NOT declared -> excluded
})


# ---------------------------------------------------------------------------
# fetch_supplementary — core package parsing / filtering
# ---------------------------------------------------------------------------

class TestFetchSupplementary:
    def _patch_fetch(self, mocks_text="METHODS: adult male C57BL/6 mice"):
        # Resolve, package URL, and download are patched; PDF text mocked.
        return (
            patch.object(supp, "_oa_package_href", return_value="ftp://x/pkg.tar.gz"),
            patch.object(supp, "_download_package", return_value=PACKAGE),
            patch("biolit.parsers.pdf.extract_pdf_text", return_value=mocks_text),
        )

    def test_returns_only_declared_supplementary_files(self):
        a, b, c = self._patch_fetch()
        with a, b, c:
            files = fetch_supplementary(pmcid="PMC1")
        names = {f.name for f in files}
        # mmc1.pdf, mmc2.xlsx, media-3.pdf declared; main + nxml excluded
        assert names == {"mmc1.pdf", "mmc2.xlsx", "media-3.pdf"}
        assert "main-article.pdf" not in names

    def test_labels_and_kinds(self):
        a, b, c = self._patch_fetch()
        with a, b, c:
            files = {f.name: f for f in fetch_supplementary(pmcid="PMC1")}
        assert files["mmc1.pdf"].label == "Supplementary Methods"
        assert files["mmc1.pdf"].kind == "pdf"
        assert "C57BL/6" in files["mmc1.pdf"].text
        assert files["mmc2.xlsx"].label == "Table S1"
        assert files["mmc2.xlsx"].kind == "other"      # xlsx not text-extractable
        assert files["mmc2.xlsx"].text == ""
        # ext-less JATS href ("media-3") still matches the packaged media-3.pdf
        assert files["media-3.pdf"].kind == "pdf"
        assert files["media-3.pdf"].label == "Supplementary Data"

    def test_extract_false_lists_without_text(self):
        a, b, _ = self._patch_fetch()
        with a, b:
            files = fetch_supplementary(pmcid="PMC1", extract=False)
        assert {f.name for f in files} == {"mmc1.pdf", "mmc2.xlsx", "media-3.pdf"}
        assert all(f.text == "" for f in files)
        assert {f.kind for f in files if f.name.endswith(".pdf")} == {"pdf"}

    def test_no_pmcid_resolved_returns_empty(self):
        assert fetch_supplementary() == []

    def test_no_oa_package_returns_empty(self):
        with patch.object(supp, "_oa_package_href", return_value=None):
            assert fetch_supplementary(pmcid="PMC1") == []

    def test_download_failure_returns_empty(self):
        with patch.object(supp, "_oa_package_href", return_value="ftp://x/p.tgz"), \
             patch.object(supp, "_download_package", return_value=None):
            assert fetch_supplementary(pmcid="PMC1") == []

    def test_no_declared_supplementary_returns_empty(self):
        pkg = _make_package({
            "PMC2/article.nxml": b"<article><body><p>no supp</p></body></article>",
            "PMC2/main.pdf": b"%PDF-1.4 main",
        })
        with patch.object(supp, "_oa_package_href", return_value="ftp://x/p.tgz"), \
             patch.object(supp, "_download_package", return_value=pkg):
            assert fetch_supplementary(pmcid="PMC2") == []

    def test_resolves_pmid_to_pmcid(self):
        a, b, c = self._patch_fetch()
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
# _oa_package_href — parsing the OA service response
# ---------------------------------------------------------------------------

def _resp(text="", status=200):
    r = MagicMock()
    r.text = text
    r.raise_for_status = MagicMock()
    if status >= 400:
        r.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return r


class TestOaPackageHref:
    OA_XML = (
        '<OA><records><record id="PMC1">'
        '<link format="tgz" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/aa/bb/PMC1.tar.gz" />'
        '<link format="pdf" href="ftp://x/PMC1.pdf" />'
        '</record></records></OA>'
    )

    @patch("biolit.fetchers.supplementary.requests.get")
    def test_extracts_tgz_href(self, mock_get):
        mock_get.return_value = _resp(self.OA_XML)
        assert supp._oa_package_href("PMC1").endswith("PMC1.tar.gz")

    @patch("biolit.fetchers.supplementary.requests.get")
    def test_none_when_no_tgz(self, mock_get):
        mock_get.return_value = _resp("<OA><records><record/></records></OA>")
        assert supp._oa_package_href("PMC1") is None

    @patch("biolit.fetchers.supplementary.requests.get")
    def test_none_on_error(self, mock_get):
        mock_get.side_effect = ConnectionError("down")
        assert supp._oa_package_href("PMC1") is None


# ---------------------------------------------------------------------------
# _download_package — https-mirror-first, gzip-magic check
# ---------------------------------------------------------------------------

class TestDownloadPackage:
    @patch("biolit.fetchers.supplementary.requests.get")
    def test_https_mirror_success(self, mock_get):
        r = MagicMock()
        r.content = b"\x1f\x8b gzipped"
        r.raise_for_status = MagicMock()
        mock_get.return_value = r
        out = supp._download_package("ftp://ftp.ncbi.nlm.nih.gov/p/PMC1.tar.gz")
        assert out.startswith(b"\x1f\x8b")
        # rewrote ftp:// -> https:// for the requests call
        assert mock_get.call_args[0][0].startswith("https://")

    @patch("biolit.fetchers.supplementary.requests.get")
    def test_rejects_non_gzip(self, mock_get):
        r = MagicMock()
        r.content = b"<html>not found</html>"
        r.raise_for_status = MagicMock()
        mock_get.return_value = r
        # https returns non-gzip; ftp fallback also fails (urlopen raises)
        with patch("urllib.request.urlopen", side_effect=Exception("no ftp")):
            assert supp._download_package("ftp://x/PMC1.tar.gz") is None

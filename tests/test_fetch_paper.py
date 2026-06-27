"""Unit tests for the high-level pipeline.fetch_paper resolution chain
(accession -> DOI -> bare PMID), with all network mocked."""
from unittest.mock import patch

from biolit.pipeline import fetch_paper, PaperResult


GEO_NO_LINK = {"geo_accession": "GSE999", "pmid": None, "doi": None,
               "abstract": "geo abstract", "title": "GEO series"}
PUBMED_REC = {"geo_accession": None, "pmid": "12345", "doi": "10.1/x",
              "abstract": "abs", "title": "The paper"}


def _records(id_str):
    return {"GSE999": GEO_NO_LINK, "12345": PUBMED_REC}.get(id_str)


@patch("biolit.pipeline.fetch_record", side_effect=_records)
@patch("biolit.pipeline._resolve_geo_fulltext",
       side_effect=lambda p, *a, **kw: ("GEO metadata only", "geo_record", {}))
@patch("biolit.pipeline.resolve_fulltext",
       side_effect=lambda p, *a, **kw: ("FULL METHODS TEXT", "pmc_fulltext", {}))
def test_bare_pmid_fallback_when_geo_has_no_linked_pub(mock_rf, mock_geo, mock_rec):
    """GEO record has no linked publication, so the accession path yields only
    a bare GEO record. A caller-supplied PMID (e.g. from a title search) must
    be fetched directly — the documented 'bare PMID last fallback' (step 3)."""
    r = fetch_paper(accession="GSE999", pmid="12345")
    assert isinstance(r, PaperResult)
    assert r.is_fulltext is True
    assert r.source == "pmc_fulltext"
    assert r.text == "FULL METHODS TEXT"


@patch("biolit.pipeline.fetch_record", side_effect=_records)
@patch("biolit.pipeline._resolve_geo_fulltext",
       side_effect=lambda p, *a, **kw: ("GEO-LINKED FULLTEXT", "geo_linked_fulltext", {}))
@patch("biolit.pipeline.resolve_fulltext",
       side_effect=lambda p, *a, **kw: ("should not be reached", "pmc_fulltext", {}))
def test_accession_fulltext_short_circuits(mock_rf, mock_geo, mock_rec):
    """When the accession path already yields full text, the PMID fallback is
    NOT attempted (no wasted fetch)."""
    r = fetch_paper(accession="GSE999", pmid="12345")
    assert r.is_fulltext is True
    assert r.source == "geo_linked_fulltext"
    mock_rf.assert_not_called()


@patch("biolit.pipeline.fetch_record", side_effect=_records)
@patch("biolit.pipeline._resolve_geo_fulltext",
       side_effect=lambda p, *a, **kw: ("GEO metadata only", "geo_record", {}))
@patch("biolit.pipeline.resolve_fulltext",
       side_effect=lambda p, *a, **kw: ("FULL METHODS TEXT", "pmc_fulltext", {}))
def test_no_pmid_returns_bare_geo_record(mock_rf, mock_geo, mock_rec):
    """Without a PMID there is nothing to fall back to — return the GEO record
    (not full text). This is the pre-fix behaviour for GEO-unlinked papers."""
    r = fetch_paper(accession="GSE999")
    assert r.is_fulltext is False
    assert r.source == "geo_record"
    mock_rf.assert_not_called()

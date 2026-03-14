"""Tests for GEO fetcher parsing logic (no network calls)."""
import pytest

from pubmed_screener.fetchers.geo import _parse_miniml

MINIML_WITH_NS = b"""<?xml version="1.0" encoding="UTF-8"?>
<MINiML xmlns="http://www.ncbi.nlm.nih.gov/geo/info/MINiML">
  <Series iid="GSE53987">
    <Title>Microarray profiling of PFC, HPC and STR</Title>
    <Accession database="GEO">GSE53987</Accession>
    <Summary>Gene expression profiling of postmortem brain tissue from subjects with schizophrenia.</Summary>
    <Overall-Design>Matched cases and controls, n=19 per group.</Overall-Design>
    <Type>Expression profiling by array</Type>
    <Pubmed-ID>31123247</Pubmed-ID>
    <Sample iid="GSM1">
      <Organism>Homo sapiens</Organism>
    </Sample>
    <Sample iid="GSM2">
      <Organism>Homo sapiens</Organism>
    </Sample>
  </Series>
</MINiML>
"""

MINIML_NO_NS = b"""<?xml version="1.0" encoding="UTF-8"?>
<MINiML>
  <Series iid="GSE99999">
    <Title>Test series without namespace</Title>
    <Summary>A summary.</Summary>
    <Overall-Design>Simple design.</Overall-Design>
    <Type>RNA-Seq</Type>
    <Pubmed-ID>12345678</Pubmed-ID>
    <Pubmed-ID>87654321</Pubmed-ID>
  </Series>
</MINiML>
"""

MINIML_MISSING_FIELDS = b"""<?xml version="1.0" encoding="UTF-8"?>
<MINiML xmlns="http://www.ncbi.nlm.nih.gov/geo/info/MINiML">
  <Series iid="GSE00001">
    <Title>Minimal series</Title>
  </Series>
</MINiML>
"""


class TestParseMiniml:
    def test_parses_title(self):
        result = _parse_miniml("GSE53987", MINIML_WITH_NS)
        assert result["title"] == "Microarray profiling of PFC, HPC and STR"

    def test_parses_summary_into_abstract(self):
        result = _parse_miniml("GSE53987", MINIML_WITH_NS)
        assert "Gene expression profiling" in result["abstract"]

    def test_parses_overall_design_into_abstract(self):
        result = _parse_miniml("GSE53987", MINIML_WITH_NS)
        assert "Matched cases and controls" in result["abstract"]

    def test_parses_experiment_type_into_abstract(self):
        result = _parse_miniml("GSE53987", MINIML_WITH_NS)
        assert "Expression profiling by array" in result["abstract"]

    def test_parses_single_pmid(self):
        result = _parse_miniml("GSE53987", MINIML_WITH_NS)
        assert result["pmids"] == ["31123247"]
        assert result["pmid"] == "31123247"

    def test_parses_multiple_pmids(self):
        result = _parse_miniml("GSE99999", MINIML_NO_NS)
        assert result["pmids"] == ["12345678", "87654321"]
        assert result["pmid"] == "12345678"

    def test_no_pmids_sets_pmid_to_none(self):
        result = _parse_miniml("GSE00001", MINIML_MISSING_FIELDS)
        assert result["pmid"] is None
        assert result["pmids"] == []

    def test_url_contains_accession(self):
        result = _parse_miniml("GSE53987", MINIML_WITH_NS)
        assert "GSE53987" in result["url"]

    def test_accession_stored(self):
        result = _parse_miniml("GSE53987", MINIML_WITH_NS)
        assert result["accession"] == "GSE53987"

    def test_text_source_is_geo_record(self):
        result = _parse_miniml("GSE53987", MINIML_WITH_NS)
        assert result["text_source"] == "geo_record"

    def test_handles_missing_optional_fields(self):
        result = _parse_miniml("GSE00001", MINIML_MISSING_FIELDS)
        assert result is not None
        assert result["title"] == "Minimal series"
        assert result["abstract"] == ""

    def test_returns_none_for_invalid_xml(self):
        result = _parse_miniml("GSE00001", b"this is not xml <<<")
        assert result is None

    def test_returns_none_when_no_series_element(self):
        xml = b"""<?xml version="1.0"?><MINiML><Platform iid="GPL1"/></MINiML>"""
        result = _parse_miniml("GSE00001", xml)
        assert result is None

    def test_handles_namespace(self):
        with_ns = _parse_miniml("GSE53987", MINIML_WITH_NS)
        without_ns = _parse_miniml("GSE99999", MINIML_NO_NS)
        assert with_ns is not None
        assert without_ns is not None

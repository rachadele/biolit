"""Tests for GEO fetcher parsing logic (no network calls)."""
import pytest

from biolit.fetchers.geo import _parse_miniml, format_geo_metadata

MINIML_WITH_NS = b"""<?xml version="1.0" encoding="UTF-8"?>
<MINiML xmlns="http://www.ncbi.nlm.nih.gov/geo/info/MINiML">
  <Platform iid="GPL570">
    <Title>Affymetrix Human Genome U133 Plus 2.0 Array</Title>
    <Accession database="GEO">GPL570</Accession>
    <Technology>in situ oligonucleotide</Technology>
    <Organism taxid="9606">Homo sapiens</Organism>
  </Platform>
  <Series iid="GSE53987">
    <Title>Microarray profiling of PFC, HPC and STR</Title>
    <Accession database="GEO">GSE53987</Accession>
    <Summary>Gene expression profiling of postmortem brain tissue from subjects with schizophrenia.</Summary>
    <Overall-Design>Matched cases and controls, n=19 per group.</Overall-Design>
    <Type>Expression profiling by array</Type>
    <Pubmed-ID>31123247</Pubmed-ID>
    <Sample-Ref ref="GSM1"/>
    <Sample-Ref ref="GSM2"/>
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
    def test_parses_complete_record(self):
        result = _parse_miniml("GSE53987", MINIML_WITH_NS)
        assert result["title"] == "Microarray profiling of PFC, HPC and STR"
        assert result["accession"] == "GSE53987"
        assert "GSE53987" in result["url"]
        assert result["text_source"] == "geo_record"
        assert result["pmids"] == ["31123247"]
        assert result["pmid"] == "31123247"

    def test_abstract_combines_summary_design_and_type(self):
        result = _parse_miniml("GSE53987", MINIML_WITH_NS)
        assert "Gene expression profiling" in result["abstract"]
        assert "Matched cases and controls" in result["abstract"]
        assert "Expression profiling by array" in result["abstract"]

    def test_parses_multiple_pmids(self):
        result = _parse_miniml("GSE99999", MINIML_NO_NS)
        assert result["pmids"] == ["12345678", "87654321"]
        assert result["pmid"] == "12345678"

    def test_handles_missing_optional_fields(self):
        result = _parse_miniml("GSE00001", MINIML_MISSING_FIELDS)
        assert result is not None
        assert result["title"] == "Minimal series"
        assert result["abstract"] == ""
        assert result["pmid"] is None
        assert result["pmids"] == []

    def test_returns_none_for_invalid_xml(self):
        assert _parse_miniml("GSE00001", b"this is not xml <<<") is None

    def test_returns_none_when_no_series_element(self):
        xml = b"""<?xml version="1.0"?><MINiML><Platform iid="GPL1"/></MINiML>"""
        assert _parse_miniml("GSE00001", xml) is None

    def test_handles_both_namespace_variants(self):
        assert _parse_miniml("GSE53987", MINIML_WITH_NS) is not None
        assert _parse_miniml("GSE99999", MINIML_NO_NS) is not None

    def test_parses_platform_accession_and_title(self):
        result = _parse_miniml("GSE53987", MINIML_WITH_NS)
        assert len(result["platforms"]) == 1
        plat = result["platforms"][0]
        assert plat["accession"] == "GPL570"
        assert plat["title"] == "Affymetrix Human Genome U133 Plus 2.0 Array"
        assert plat["technology"] == "in situ oligonucleotide"

    def test_organism_from_platform(self):
        result = _parse_miniml("GSE53987", MINIML_WITH_NS)
        assert "Homo sapiens" in result["organisms"]

    def test_sample_count_from_sample_refs(self):
        result = _parse_miniml("GSE53987", MINIML_WITH_NS)
        assert result["sample_count"] == 2

    def test_no_platforms_when_absent(self):
        result = _parse_miniml("GSE99999", MINIML_NO_NS)
        assert result["platforms"] == []
        assert result["organisms"] == []
        assert result["sample_count"] == 0


class TestFormatGeoMetadata:
    def test_includes_accession_header(self):
        record = _parse_miniml("GSE53987", MINIML_WITH_NS)
        text = format_geo_metadata(record)
        assert "GSE53987" in text

    def test_includes_platform_info(self):
        record = _parse_miniml("GSE53987", MINIML_WITH_NS)
        text = format_geo_metadata(record)
        assert "GPL570" in text
        assert "Affymetrix Human Genome U133 Plus 2.0 Array" in text

    def test_includes_organism(self):
        record = _parse_miniml("GSE53987", MINIML_WITH_NS)
        text = format_geo_metadata(record)
        assert "Homo sapiens" in text

    def test_includes_summary_and_design(self):
        record = _parse_miniml("GSE53987", MINIML_WITH_NS)
        text = format_geo_metadata(record)
        assert "Gene expression profiling" in text
        assert "Matched cases and controls" in text

    def test_includes_sample_count(self):
        record = _parse_miniml("GSE53987", MINIML_WITH_NS)
        text = format_geo_metadata(record)
        assert "2" in text  # sample count

    def test_no_raw_xml_in_output(self):
        record = _parse_miniml("GSE53987", MINIML_WITH_NS)
        text = format_geo_metadata(record)
        assert "<MINiML" not in text
        assert "<Series" not in text

    def test_handles_missing_fields_gracefully(self):
        record = _parse_miniml("GSE00001", MINIML_MISSING_FIELDS)
        text = format_geo_metadata(record)
        assert "GSE00001" in text  # at minimum the accession is present

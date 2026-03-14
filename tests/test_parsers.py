"""Unit tests for the JATS XML and section-selection parsers."""
import pytest

from biolit.parsers.jats import parse_jats_sections
from biolit.parsers.utils import select_sections


class TestParseJatsSections:
    def test_extracts_known_sections(self, sample_jats_xml):
        secs = parse_jats_sections(sample_jats_xml)
        assert "introduction" in secs
        assert "methods" in secs
        assert "results" in secs
        assert "discussion" in secs

    def test_extracts_abstract_element(self, sample_jats_xml):
        secs = parse_jats_sections(sample_jats_xml)
        assert "abstract" in secs
        assert "structured abstract" in secs["abstract"].lower()

    def test_methods_content_present(self, sample_jats_xml):
        secs = parse_jats_sections(sample_jats_xml)
        assert "SAIGE" in secs["methods"] or "genotyped" in secs["methods"]

    def test_results_content_present(self, sample_jats_xml):
        secs = parse_jats_sections(sample_jats_xml)
        assert "47" in secs["results"]

    def test_returns_empty_dict_on_garbage_input(self):
        result = parse_jats_sections(b"this is not xml <<<")
        assert isinstance(result, dict)

    def test_returns_dict_on_empty_bytes(self):
        result = parse_jats_sections(b"")
        assert isinstance(result, dict)


class TestSelectSections:
    def test_returns_all_sections_when_wanted_is_none(self):
        secs = {"methods": "We did X.", "results": "We found Y."}
        out = select_sections(secs, wanted=None)
        assert "METHODS" in out
        assert "RESULTS" in out

    def test_filters_to_wanted_sections(self):
        secs = {"abstract": "A.", "methods": "M.", "results": "R.", "discussion": "D."}
        out = select_sections(secs, wanted=["methods", "results"])
        assert "METHODS" in out
        assert "RESULTS" in out
        assert "ABSTRACT" not in out
        assert "DISCUSSION" not in out

    def test_falls_back_to_all_when_wanted_not_matched(self):
        secs = {"body": "Full text here."}
        out = select_sections(secs, wanted=["methods"])
        # No methods section exists; should include everything
        assert "Full text here." in out

    def test_truncates_to_max_chars(self):
        secs = {"body": "x" * 5000}
        out = select_sections(secs, max_chars=100)
        assert len(out) <= 200  # header + truncation marker add a little overhead
        assert "truncated" in out

    def test_returns_empty_string_for_empty_sections(self):
        assert select_sections({}) == ""

    def test_section_headers_are_uppercased(self):
        secs = {"introduction": "Some text."}
        out = select_sections(secs)
        assert "=== INTRODUCTION ===" in out


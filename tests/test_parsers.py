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

    def test_block_boundaries_are_separated_inline_tags_stay_glued(self):
        """Per 2026-05-09 audit: heading/paragraph block boundaries
        used to glue (`MethodsContact`, `DetailsMice`, `MiceWe`)
        because ``''.join(itertext())`` had no separator. Inline tags
        like ``<sup>`` mid-token must NOT introduce a separator —
        compound terms (``Foxp3creYFP``, ``HDAC6KO``, ``mtND6mut``)
        depend on staying glued."""
        xml = (
            b"<article><body>"
            b"<sec><title>Methods</title>"
            b"<p>Contact for Reagent Sharing</p>"
            b"<sec><title>Mice</title>"
            b"<p>We used Foxp3<sup>creYFP</sup>Mice and "
            b"HDAC6<sup>KO</sup> animals.</p>"
            b"</sec></sec></body></article>"
        )
        secs = parse_jats_sections(xml)
        methods = secs.get("methods", "")
        # Block boundaries split.
        assert "MethodsContact" not in methods
        assert "SharingMice" not in methods
        assert "MiceWe" not in methods
        # Inline-tag-bridged compound terms stay intact.
        assert "Foxp3creYFPMice" in methods
        assert "HDAC6KO" in methods


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

    def test_truncates_to_max_tokens(self):
        secs = {"body": "x" * 5000}
        out = select_sections(secs, max_tokens=25)  # 25 tokens * 4 = 100 chars
        assert len(out) <= 200  # header + truncation marker add a little overhead
        assert "truncated" in out

    def test_returns_empty_string_for_empty_sections(self):
        assert select_sections({}) == ""

    def test_section_headers_are_uppercased(self):
        secs = {"introduction": "Some text."}
        out = select_sections(secs)
        assert "=== INTRODUCTION ===" in out


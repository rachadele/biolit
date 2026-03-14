"""Unit tests for biolit.utils — pure logic, no I/O."""
import pathlib
import pytest

from biolit.utils import extract_pmids, parse_json_response, read_eml_body

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


class TestExtractPmids:
    def test_finds_two_pmids_in_stub_body(self, eml_path):
        body = read_eml_body(str(eml_path))
        pmids = extract_pmids(body)
        assert pmids == ["41795042", "41792186"]

    def test_deduplicates_repeated_pmids(self):
        body = "PMID: 99999999\nSome text\nPMID: 99999999\nPMID: 88888888"
        assert extract_pmids(body) == ["99999999", "88888888"]

    def test_returns_empty_list_when_none_found(self):
        assert extract_pmids("No identifiers here.") == []

    def test_preserves_order(self):
        body = "PMID: 3\nPMID: 1\nPMID: 2"
        assert extract_pmids(body) == ["3", "1", "2"]


class TestParseJsonResponse:
    def test_plain_json(self):
        result = parse_json_response('{"relevant": true, "reason": "matches"}')
        assert result == {"relevant": True, "reason": "matches"}

    def test_strips_json_code_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        assert parse_json_response(raw) == {"key": "value"}

    def test_strips_plain_code_fence(self):
        raw = '```\n{"key": 1}\n```'
        assert parse_json_response(raw) == {"key": 1}

    def test_raises_on_invalid_json(self):
        with pytest.raises(Exception):
            parse_json_response("not json at all")


class TestReadEmlBody:
    def test_reads_stub_eml_and_contains_pmids(self, eml_path):
        body = read_eml_body(str(eml_path))
        assert "41795042" in body
        assert "41792186" in body

    def test_returns_string(self, eml_path):
        body = read_eml_body(str(eml_path))
        assert isinstance(body, str)
        assert len(body) > 0


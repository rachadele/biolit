"""Integration tests for the unified pipeline, with all network and LLM calls mocked."""
import csv
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from biolit.fetchers.pubmed import fetch_pubmed_metadata
from biolit.llm.base import BaseLLMClient
from biolit.pipeline import (
    run,
    build_output_schema,
    screen_paper,
    extract_fields,
    format_record_markdown,
    generate_markdown_summary,
    screen_by_doi,
    screen_by_pmid,
    resolve_fulltext,
    _resolve_geo_fulltext,
)

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fake LLM client
# ---------------------------------------------------------------------------

class FakeLLMClient(BaseLLMClient):
    """Deterministic LLM that returns scripted JSON responses in sequence."""

    def __init__(self, responses: list[str]):
        super().__init__(model="fake-model")
        self._responses = list(responses)
        self._index = 0
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict], max_tokens: int = 512) -> str:
        self.calls.append(messages)
        if self._index >= len(self._responses):
            return "{}"
        resp = self._responses[self._index]
        self._index += 1
        return resp


# ---------------------------------------------------------------------------
# Fake records returned by fetch_record / fetch_pubmed_metadata
# ---------------------------------------------------------------------------

FAKE_PAPER_1 = {
    "pmid": "41795042",
    "doi": "10.1038/s41588-026-01234-5",
    "geo_accession": None,
    "title": "Genome-wide association study of schizophrenia in a European cohort",
    "abstract": "GWAS of schizophrenia in 130,000 individuals identified 47 loci.",
    "mesh_terms": ["Schizophrenia", "Genome-Wide Association Study"],
    "url": "https://pubmed.ncbi.nlm.nih.gov/41795042/",
}

FAKE_PAPER_2 = {
    "pmid": "41792186",
    "doi": "10.1016/j.biopsych.2026.01.005",
    "geo_accession": None,
    "title": "Transcriptomic profiling of prefrontal cortex in schizophrenia",
    "abstract": "scRNA-seq of 200,000 nuclei from schizophrenia patients and controls.",
    "mesh_terms": ["Schizophrenia", "Transcriptome", "Prefrontal Cortex"],
    "url": "https://pubmed.ncbi.nlm.nih.gov/41792186/",
}

FAKE_GEO_RECORD = {
    "pmid": "31123247",
    "accession": "GSE53987",
    "geo_accession": "GSE53987",
    "doi": None,
    "title": "Microarray profiling of PFC, HPC and STR from subjects with schizophrenia",
    "abstract": "Summary: Gene expression profiling of postmortem brain tissue.\n\nOverall design: Matched cases and controls.",
    "mesh_terms": ["Expression profiling by array", "Homo sapiens"],
    "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE53987",
    "pmids": ["31123247"],
    "text_source": "geo_record",
}


# ---------------------------------------------------------------------------
# Unit tests for individual pipeline functions
# ---------------------------------------------------------------------------

class TestBuildOutputSchema:
    def test_returns_dict_with_expected_keys(self):
        schema_json = '{"methodology": "method used", "summary": "brief summary"}'
        client = FakeLLMClient([schema_json])
        result = build_output_schema(client, "methodology, summary")
        assert isinstance(result, dict)
        assert "methodology" in result
        assert "summary" in result
        assert len(client.calls) == 1

    def test_passes_fields_in_prompt(self):
        client = FakeLLMClient(['{"foo": "bar"}'])
        build_output_schema(client, "foo")
        prompt_text = client.calls[0][0]["content"]
        assert "foo" in prompt_text


class TestScreenPaper:
    def test_returns_relevant_true(self, sample_pubmed_metadata):
        response = '{"relevant": true, "reason": "Uses GWAS to study schizophrenia."}'
        client = FakeLLMClient([response])
        result = screen_paper(client, sample_pubmed_metadata, "Is this about schizophrenia genomics?", "abstract text")
        assert result["relevant"] is True
        assert "reason" in result

    def test_returns_relevant_false(self, sample_pubmed_metadata):
        response = '{"relevant": false, "reason": "Not about schizophrenia."}'
        client = FakeLLMClient([response])
        result = screen_paper(client, sample_pubmed_metadata, "Any criterion", "abstract text")
        assert result["relevant"] is False

    def test_prompt_includes_title_and_criterion(self, sample_pubmed_metadata):
        client = FakeLLMClient(['{"relevant": true, "reason": "ok"}'])
        screen_paper(client, sample_pubmed_metadata, "MY CRITERION", "some text")
        prompt = client.calls[0][0]["content"]
        assert "MY CRITERION" in prompt
        assert sample_pubmed_metadata["title"] in prompt


class TestExtractFields:
    def test_returns_dict_with_schema_keys_and_metadata(self, sample_pubmed_metadata):
        schema = {"methodology": "method", "summary": "summary"}
        response = '{"methodology": "GWAS", "summary": "Large GWAS study."}'
        client = FakeLLMClient([response])
        result = extract_fields(client, sample_pubmed_metadata, schema, "text")
        assert result["methodology"] == "GWAS"
        assert result["title"] == sample_pubmed_metadata["title"]
        assert result["pmid"] == sample_pubmed_metadata["pmid"]
        assert result["url"] == sample_pubmed_metadata["url"]

    def test_null_fields_preserved(self, sample_pubmed_metadata):
        schema = {"rare_field": "something obscure"}
        response = '{"rare_field": null}'
        client = FakeLLMClient([response])
        result = extract_fields(client, sample_pubmed_metadata, schema, "text")
        assert result["rare_field"] is None

    def test_geo_accession_included(self):
        paper = {**FAKE_GEO_RECORD}
        schema = {"summary": "brief summary"}
        response = '{"summary": "Brain study."}'
        client = FakeLLMClient([response])
        result = extract_fields(client, paper, schema, "text")
        assert result["geo_accession"] == "GSE53987"
        assert result["pmid"] == "31123247"


# ---------------------------------------------------------------------------
# Full end-to-end pipeline integration tests
# ---------------------------------------------------------------------------

def _find_csv(tmp_path: pathlib.Path) -> pathlib.Path | None:
    """Return the results.csv written inside a run_* subdirectory, or None."""
    matches = list(tmp_path.rglob("results.csv"))
    return matches[0] if matches else None


class TestPipelineRun:
    """Run the complete pipeline with mocked network calls and a fake LLM."""

    def _make_client(self, paper1_relevant=True, paper2_relevant=False):
        schema_resp = '{"methodology": "experimental method", "summary": "brief summary"}'
        screen_1 = json.dumps({"relevant": paper1_relevant, "reason": "Matches criterion."})
        screen_2 = json.dumps({"relevant": paper2_relevant, "reason": "Does not match."})
        extract_1 = '{"methodology": "GWAS", "summary": "Large GWAS of schizophrenia."}'
        responses = [schema_resp, screen_1]
        if paper1_relevant:
            responses.append(extract_1)
        responses.append(screen_2)
        if paper2_relevant:
            responses.append('{"methodology": "scRNA-seq", "summary": "Transcriptomics study."}')
        return FakeLLMClient(responses)

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.resolve_fulltext", side_effect=lambda p, *a, **kw: (p.get("abstract", ""), "abstract", {}))
    @patch("biolit.pipeline.fetch_record")
    def test_one_relevant_paper_writes_csv(self, mock_fetch, mock_resolve, mock_citations, eml_path, tmp_path):
        mock_fetch.side_effect = [FAKE_PAPER_1, FAKE_PAPER_2]
        mock_citations.return_value = 99
        client = self._make_client(paper1_relevant=True, paper2_relevant=False)
        output = tmp_path / "results.csv"

        run(
            client=client,
            ids=["41795042", "41792186"],
            criterion="Is this about schizophrenia genomics?",
            fields_description="methodology, summary",
            output_path=str(output),
        )

        csv_path = _find_csv(tmp_path)
        assert csv_path is not None, "CSV output file should be created"
        rows = list(csv.DictReader(csv_path.open()))
        assert len(rows) == 1
        assert rows[0]["pmid"] == "41795042"
        assert rows[0]["methodology"] == "GWAS"
        assert rows[0]["text_source"] == "abstract"

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.resolve_fulltext", side_effect=lambda p, *a, **kw: (p.get("abstract", ""), "abstract", {}))
    @patch("biolit.pipeline.fetch_record")
    def test_no_relevant_papers_no_csv(self, mock_fetch, mock_resolve, mock_citations, eml_path, tmp_path):
        mock_fetch.side_effect = [FAKE_PAPER_1, FAKE_PAPER_2]
        mock_citations.return_value = None
        schema_resp = '{"methodology": "method"}'
        screen_resp = '{"relevant": false, "reason": "Nope."}'
        client = FakeLLMClient([schema_resp, screen_resp, screen_resp])
        output = tmp_path / "results.csv"

        run(
            client=client,
            ids=["41795042", "41792186"],
            criterion="Unrelated criterion",
            fields_description="methodology",
            output_path=str(output),
        )

        assert _find_csv(tmp_path) is None, "CSV should not be created when no papers are relevant"

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.resolve_fulltext", side_effect=lambda p, *a, **kw: (p.get("abstract", ""), "abstract", {}))
    @patch("biolit.pipeline.fetch_record")
    def test_fetch_error_is_skipped_gracefully(self, mock_fetch, mock_resolve, mock_citations, eml_path, tmp_path):
        mock_fetch.side_effect = [Exception("Network error"), FAKE_PAPER_2]
        mock_citations.return_value = None
        schema_resp = '{"methodology": "method"}'
        screen_resp = '{"relevant": false, "reason": "Nope."}'
        client = FakeLLMClient([schema_resp, screen_resp])
        output = tmp_path / "results.csv"

        # Should not raise even though the first fetch fails
        run(
            client=client,
            ids=["41795042", "41792186"],
            criterion="Any",
            fields_description="methodology",
            output_path=str(output),
        )

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.fetch_pmc_fulltext")
    @patch("biolit.pipeline.fetch_record")
    def test_fulltext_pmc_used_when_available(self, mock_fetch, mock_pmc, mock_citations, eml_path, tmp_path, sample_jats_xml):
        mock_fetch.side_effect = [FAKE_PAPER_1, FAKE_PAPER_2]
        mock_pmc.side_effect = [sample_jats_xml, None]
        mock_citations.return_value = None

        schema_resp = '{"methodology": "method", "summary": "summary"}'
        screen_1 = '{"relevant": true, "reason": "Matches."}'
        extract_1 = '{"methodology": "GWAS", "summary": "Full text study."}'
        screen_2 = '{"relevant": false, "reason": "No."}'
        client = FakeLLMClient([schema_resp, screen_1, extract_1, screen_2])
        output = tmp_path / "results.csv"

        run(
            client=client,
            ids=["41795042", "41792186"],
            criterion="Is this about schizophrenia?",
            fields_description="methodology, summary",
            output_path=str(output),
        )

        csv_path = _find_csv(tmp_path)
        assert csv_path is not None
        rows = list(csv.DictReader(csv_path.open()))
        assert len(rows) == 1
        assert rows[0]["text_source"] == "pmc_fulltext"

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.resolve_fulltext", side_effect=lambda p, *a, **kw: (p.get("abstract", ""), "abstract", {}))
    @patch("biolit.pipeline.fetch_record")
    def test_csv_contains_required_columns(self, mock_fetch, mock_resolve, mock_citations, eml_path, tmp_path):
        mock_fetch.side_effect = [FAKE_PAPER_1, FAKE_PAPER_2]
        mock_citations.return_value = 5
        client = self._make_client(paper1_relevant=True, paper2_relevant=False)
        output = tmp_path / "results.csv"

        run(
            client=client,
            ids=["41795042", "41792186"],
            criterion="Any",
            fields_description="methodology, summary",
            output_path=str(output),
        )

        csv_path = _find_csv(tmp_path)
        assert csv_path is not None
        reader = csv.DictReader(csv_path.open())
        required = {"title", "url", "pmid", "doi", "geo_accession", "text_source", "citation_count"}
        assert required.issubset(set(reader.fieldnames))

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.resolve_fulltext", side_effect=lambda p, *a, **kw: (p.get("abstract", ""), "abstract", {}))
    @patch("biolit.pipeline.fetch_record")
    def test_citation_count_written_to_csv(self, mock_fetch, mock_resolve, mock_citations, eml_path, tmp_path):
        mock_fetch.side_effect = [FAKE_PAPER_1, FAKE_PAPER_2]
        mock_citations.return_value = 42
        client = self._make_client(paper1_relevant=True, paper2_relevant=False)
        output = tmp_path / "results.csv"

        run(
            client=client,
            ids=["41795042", "41792186"],
            criterion="Any",
            fields_description="methodology, summary",
            output_path=str(output),
        )

        csv_path = _find_csv(tmp_path)
        rows = list(csv.DictReader(csv_path.open()))
        assert rows[0]["citation_count"] == "42"


# ---------------------------------------------------------------------------
# GEO records through the unified pipeline
# ---------------------------------------------------------------------------

class TestPipelineRunGeo:
    def _make_client(self, relevant=True):
        schema_resp = '{"methodology": "experimental method", "summary": "brief summary"}'
        screen_resp = json.dumps({"relevant": relevant, "reason": "Matches." if relevant else "Does not match."})
        responses = [schema_resp, screen_resp]
        if relevant:
            responses.append('{"methodology": "Microarray", "summary": "Brain expression study."}')
        return FakeLLMClient(responses)

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.fetch_pubmed_metadata", return_value=None)
    @patch("biolit.pipeline.fetch_record")
    def test_relevant_geo_record_writes_csv(self, mock_fetch, mock_fetch_pm, mock_citations, tmp_path):
        mock_fetch.return_value = FAKE_GEO_RECORD
        mock_citations.return_value = None
        client = self._make_client(relevant=True)
        output = tmp_path / "results.csv"

        run(
            client=client,
            ids=["GSE53987"],
            criterion="Is this a schizophrenia gene expression study?",
            fields_description="methodology, summary",
            output_path=str(output),
        )

        csv_path = _find_csv(tmp_path)
        assert csv_path is not None
        rows = list(csv.DictReader(csv_path.open()))
        assert len(rows) == 1
        assert rows[0]["geo_accession"] == "GSE53987"
        assert rows[0]["linked_pmids"] == "31123247"
        assert rows[0]["text_source"] == "geo_record"

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.fetch_record")
    def test_irrelevant_geo_record_no_csv(self, mock_fetch, mock_citations, tmp_path):
        mock_fetch.return_value = FAKE_GEO_RECORD
        mock_citations.return_value = None
        client = self._make_client(relevant=False)
        output = tmp_path / "results.csv"

        run(
            client=client,
            ids=["GSE53987"],
            criterion="Is this about Arabidopsis?",
            fields_description="methodology, summary",
            output_path=str(output),
        )

        assert _find_csv(tmp_path) is None

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.fetch_record")
    def test_geo_csv_has_accession_and_linked_pmids_columns(self, mock_fetch, mock_citations, tmp_path):
        mock_fetch.return_value = FAKE_GEO_RECORD
        mock_citations.return_value = None
        client = self._make_client(relevant=True)
        output = tmp_path / "results.csv"

        run(
            client=client,
            ids=["GSE53987"],
            criterion="Any",
            fields_description="methodology, summary",
            output_path=str(output),
        )

        csv_path = _find_csv(tmp_path)
        reader = csv.DictReader(csv_path.open())
        assert "geo_accession" in reader.fieldnames
        assert "linked_pmids" in reader.fieldnames
        assert "pmid" in reader.fieldnames  # first linked PMID from the GEO record

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.fetch_record")
    def test_citation_count_uses_linked_pmid_when_no_doi(self, mock_fetch, mock_citations, tmp_path):
        mock_fetch.return_value = FAKE_GEO_RECORD
        mock_citations.return_value = 15
        client = self._make_client(relevant=True)
        output = tmp_path / "results.csv"

        run(
            client=client,
            ids=["GSE53987"],
            criterion="Any",
            fields_description="methodology, summary",
            output_path=str(output),
        )

        # GEO record has pmid="31123247" directly; doi=None
        mock_citations.assert_called_once_with(doi=None, pmid="31123247")
        csv_path = _find_csv(tmp_path)
        rows = list(csv.DictReader(csv_path.open()))
        assert rows[0]["citation_count"] == "15"


# ---------------------------------------------------------------------------
# screen_by_doi tests
# ---------------------------------------------------------------------------

FAKE_DOI = "10.64898/2026.03.05.709906"


class TestScreenByDoi:
    def _make_client(self, relevant=True):
        resp = json.dumps({"relevant": relevant, "reason": "Matches." if relevant else "No."})
        return FakeLLMClient([resp])

    @patch("biolit.pipeline.fetch_preprint_metadata")
    @patch("biolit.pipeline.fetch_s2_pdf")
    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_preprint")
    def test_uses_preprint_jats_when_available(
        self, mock_preprint, mock_epmc, mock_s2, mock_meta, sample_jats_xml
    ):
        mock_preprint.return_value = sample_jats_xml
        client = self._make_client(relevant=True)
        result = screen_by_doi(client, FAKE_DOI, "Is this relevant?")
        assert result["text_source"] == "preprint_fulltext"
        mock_epmc.assert_not_called()

    @patch("biolit.pipeline.fetch_preprint_metadata")
    @patch("biolit.pipeline.fetch_s2_pdf")
    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_preprint")
    def test_falls_back_to_europepmc(
        self, mock_preprint, mock_epmc, mock_s2, mock_meta, sample_jats_xml
    ):
        mock_preprint.return_value = None
        mock_epmc.return_value = sample_jats_xml
        client = self._make_client(relevant=True)
        result = screen_by_doi(client, FAKE_DOI, "Is this relevant?")
        assert result["text_source"] == "europepmc_fulltext"

    @patch("biolit.pipeline.fetch_preprint_metadata")
    @patch("biolit.pipeline.fetch_s2_pdf")
    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_preprint")
    def test_falls_back_to_s2_pdf(
        self, mock_preprint, mock_epmc, mock_s2, mock_meta, sample_jats_xml
    ):
        mock_preprint.return_value = None
        mock_epmc.return_value = None
        with patch("biolit.pipeline.parse_pdf_sections") as mock_parse:
            mock_s2.return_value = b"%PDF fake"
            mock_parse.return_value = {"body": "S2 full text content here."}
            client = self._make_client(relevant=True)
            result = screen_by_doi(client, FAKE_DOI, "Is this relevant?")
        assert result["text_source"] == "s2_pdf"

    @patch("biolit.pipeline.fetch_preprint_metadata")
    @patch("biolit.pipeline.fetch_s2_pdf")
    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_preprint")
    def test_falls_back_to_preprint_abstract(
        self, mock_preprint, mock_epmc, mock_s2, mock_meta
    ):
        mock_preprint.return_value = None
        mock_epmc.return_value = None
        mock_s2.return_value = None
        mock_meta.return_value = {
            "title": "LLM curation paper",
            "abstract": "We used GPT-4o for annotation.",
            "doi": FAKE_DOI,
            "server": "biorxiv",
        }
        client = self._make_client(relevant=True)
        result = screen_by_doi(client, FAKE_DOI, "Is this relevant?")
        assert result["text_source"] == "preprint_abstract"

    @patch("biolit.pipeline.fetch_preprint_metadata")
    @patch("biolit.pipeline.fetch_s2_pdf")
    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_preprint")
    def test_returns_error_when_no_content(
        self, mock_preprint, mock_epmc, mock_s2, mock_meta
    ):
        mock_preprint.return_value = None
        mock_epmc.return_value = None
        mock_s2.return_value = None
        mock_meta.return_value = None
        client = self._make_client()
        result = screen_by_doi(client, FAKE_DOI, "Is this relevant?")
        assert "error" in result

    @patch("biolit.pipeline.fetch_preprint_metadata")
    @patch("biolit.pipeline.fetch_s2_pdf")
    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_preprint")
    def test_result_includes_doi(
        self, mock_preprint, mock_epmc, mock_s2, mock_meta
    ):
        mock_preprint.return_value = None
        mock_epmc.return_value = None
        mock_s2.return_value = None
        mock_meta.return_value = {"title": "T", "abstract": "Some abstract.", "doi": FAKE_DOI, "server": "biorxiv"}
        client = self._make_client(relevant=False)
        result = screen_by_doi(client, FAKE_DOI, "Is this relevant?")
        assert result.get("doi") == FAKE_DOI


# ---------------------------------------------------------------------------
# resolve_fulltext — Semantic Scholar step
# ---------------------------------------------------------------------------

class TestResolveFulltextS2:
    @patch("biolit.pipeline.fetch_s2_pdf")
    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_pmc_fulltext")
    def test_s2_used_when_all_xml_sources_fail(
        self, mock_pmc, mock_epmc, mock_s2, sample_pubmed_metadata
    ):
        mock_pmc.return_value = None
        mock_epmc.return_value = None
        with patch("biolit.pipeline.fetch_preprint", return_value=None), \
             patch("biolit.pipeline.parse_pdf_sections") as mock_parse:
            mock_s2.return_value = b"%PDF fake"
            mock_parse.return_value = {"body": "Full text from S2."}
            text, source, artifacts = resolve_fulltext(sample_pubmed_metadata)
        assert source == "s2_pdf"
        assert "s2_pdf" in artifacts

    @patch("biolit.pipeline.fetch_s2_pdf")
    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_pmc_fulltext")
    def test_s2_not_called_when_pmc_succeeds(
        self, mock_pmc, mock_epmc, mock_s2, sample_pubmed_metadata, sample_jats_xml
    ):
        mock_pmc.return_value = sample_jats_xml
        resolve_fulltext(sample_pubmed_metadata)
        mock_s2.assert_not_called()

    @patch("biolit.pipeline.fetch_s2_pdf")
    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_pmc_fulltext")
    def test_s2_not_called_when_no_doi(
        self, mock_pmc, mock_epmc, mock_s2, sample_pubmed_metadata
    ):
        paper_no_doi = {**sample_pubmed_metadata, "doi": None}
        mock_pmc.return_value = None
        mock_epmc.return_value = None
        with patch("biolit.pipeline.fetch_preprint", return_value=None):
            resolve_fulltext(paper_no_doi)
        mock_s2.assert_not_called()

    @patch("biolit.pipeline.fetch_s2_pdf")
    @patch("biolit.pipeline.fetch_europepmc_fulltext")
    @patch("biolit.pipeline.fetch_pmc_fulltext")
    def test_pmc_skipped_when_pmid_is_none(
        self, mock_pmc, mock_epmc, mock_s2
    ):
        paper = {
            "pmid": None,
            "doi": "10.1101/2025.03.17.25324098",
            "abstract": "Preprint abstract.",
            "mesh_terms": [],
        }
        mock_epmc.return_value = None
        mock_s2.return_value = None
        with patch("biolit.pipeline.fetch_preprint", return_value=None):
            text, source, _ = resolve_fulltext(paper)
        mock_pmc.assert_not_called()
        assert source == "abstract"
        assert text == "Preprint abstract."


# ---------------------------------------------------------------------------
# screen_by_pmid
# ---------------------------------------------------------------------------

class TestScreenByPmid:
    def _make_client(self, relevant=True):
        resp = json.dumps({"relevant": relevant, "reason": "Reason."})
        return FakeLLMClient([resp])

    @patch("biolit.pipeline.resolve_fulltext")
    @patch("biolit.pipeline.fetch_pubmed_metadata")
    def test_always_calls_resolve_fulltext(self, mock_fetch, mock_resolve, sample_pubmed_metadata):
        mock_fetch.return_value = sample_pubmed_metadata
        mock_resolve.return_value = ("abstract text", "abstract", {})
        client = self._make_client()
        screen_by_pmid(client, "41795042", "Is this relevant?")
        mock_resolve.assert_called_once()

    @patch("biolit.pipeline.resolve_fulltext")
    @patch("biolit.pipeline.fetch_pubmed_metadata")
    def test_returns_error_when_paper_not_found(self, mock_fetch, mock_resolve):
        mock_fetch.return_value = None
        client = self._make_client()
        result = screen_by_pmid(client, "00000000", "Is this relevant?")
        assert "error" in result
        mock_resolve.assert_not_called()

    @patch("biolit.pipeline.resolve_fulltext")
    @patch("biolit.pipeline.fetch_pubmed_metadata")
    def test_text_source_comes_from_resolve_fulltext(self, mock_fetch, mock_resolve, sample_pubmed_metadata):
        mock_fetch.return_value = sample_pubmed_metadata
        mock_resolve.return_value = ("full text content", "pmc_fulltext", {})
        client = self._make_client()
        result = screen_by_pmid(client, "41795042", "Is this relevant?")
        assert result["text_source"] == "pmc_fulltext"


# ---------------------------------------------------------------------------
# _resolve_geo_fulltext
# ---------------------------------------------------------------------------

FAKE_LINKED_PAPER = {
    "pmid": "31123247",
    "doi": "10.1093/nar/gky1106",
    "title": "Linked paper on schizophrenia expression",
    "abstract": "Abstract of the linked paper.",
    "mesh_terms": ["Schizophrenia"],
    "url": "https://pubmed.ncbi.nlm.nih.gov/31123247/",
}

_GEO_METADATA = "=== GEO Metadata: GSE53987 ===\nType: Expression profiling by array"

GEO_PAPER_WITH_PMIDS = {
    "pmid": "31123247",
    "accession": "GSE53987",
    "geo_accession": "GSE53987",
    "doi": None,
    "title": "Microarray profiling of PFC",
    "abstract": "GEO metadata summary text.",
    "mesh_terms": [],
    "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE53987",
    "pmids": ["31123247", "99999999"],
    "geo_metadata_text": _GEO_METADATA,
    "text_source": "geo_record",
}

GEO_PAPER_NO_PMIDS = {**GEO_PAPER_WITH_PMIDS, "pmids": []}
GEO_PAPER_NO_METADATA = {**GEO_PAPER_WITH_PMIDS, "geo_metadata_text": ""}


class TestResolveGeoFulltext:
    GEO_PREFIX = _GEO_METADATA + "\n\n--- Linked Publication ---\n"

    @patch("biolit.pipeline.resolve_fulltext")
    @patch("biolit.pipeline.fetch_pubmed_metadata")
    def test_returns_linked_fulltext_when_available(self, mock_fetch_pm, mock_resolve):
        mock_fetch_pm.return_value = FAKE_LINKED_PAPER
        mock_resolve.return_value = ("full text content", "pmc_fulltext", {"pmc_xml": b"<xml/>"})

        text, source, artifacts = _resolve_geo_fulltext(GEO_PAPER_WITH_PMIDS)

        assert text == f"{self.GEO_PREFIX}full text content"
        assert source == "geo_linked_fulltext"
        assert artifacts == {"pmc_xml": b"<xml/>"}

    @patch("biolit.pipeline.resolve_fulltext")
    @patch("biolit.pipeline.fetch_pubmed_metadata")
    def test_returns_linked_abstract_when_no_fulltext(self, mock_fetch_pm, mock_resolve):
        mock_fetch_pm.return_value = FAKE_LINKED_PAPER
        mock_resolve.return_value = ("Abstract of the linked paper.", "abstract", {})

        text, source, artifacts = _resolve_geo_fulltext(GEO_PAPER_WITH_PMIDS)

        assert text == f"{self.GEO_PREFIX}Abstract of the linked paper."
        assert source == "geo_linked_abstract"
        assert artifacts == {}

    def test_falls_back_to_geo_record_when_no_pmids(self):
        text, source, artifacts = _resolve_geo_fulltext(GEO_PAPER_NO_PMIDS)

        assert text == _GEO_METADATA
        assert source == "geo_record"
        assert artifacts == {}

    @patch("biolit.pipeline.fetch_pubmed_metadata")
    def test_falls_back_to_geo_record_when_all_pmids_fail(self, mock_fetch_pm):
        mock_fetch_pm.side_effect = RuntimeError("network error")

        text, source, artifacts = _resolve_geo_fulltext(GEO_PAPER_WITH_PMIDS)

        assert text == _GEO_METADATA
        assert source == "geo_record"
        assert artifacts == {}

    @patch("biolit.pipeline.fetch_pubmed_metadata")
    def test_falls_back_to_geo_record_when_metadata_returns_none(self, mock_fetch_pm):
        mock_fetch_pm.return_value = None

        text, source, artifacts = _resolve_geo_fulltext(GEO_PAPER_WITH_PMIDS)

        assert text == _GEO_METADATA
        assert source == "geo_record"
        assert artifacts == {}

    @patch("biolit.pipeline.resolve_fulltext")
    @patch("biolit.pipeline.fetch_pubmed_metadata")
    def test_stops_at_first_pmid_with_fulltext(self, mock_fetch_pm, mock_resolve):
        mock_fetch_pm.return_value = FAKE_LINKED_PAPER
        mock_resolve.return_value = ("full text", "pmc_fulltext", {})

        _resolve_geo_fulltext(GEO_PAPER_WITH_PMIDS)

        # GEO_PAPER_WITH_PMIDS has two PMIDs; full text found on first — should not fetch second
        mock_fetch_pm.assert_called_once_with("31123247")

    @patch("biolit.pipeline.resolve_fulltext")
    @patch("biolit.pipeline.fetch_pubmed_metadata")
    def test_geo_record_text_truncated_to_max_tokens(self, mock_fetch_pm, mock_resolve):
        mock_fetch_pm.return_value = None
        paper = {**GEO_PAPER_NO_PMIDS, "abstract": "x" * 200, "geo_metadata_text": ""}

        text, source, _ = _resolve_geo_fulltext(paper, max_tokens=13)  # 13 * 4 = 52 chars

        assert "x" * 50 in text
        assert "x" * 53 not in text
        assert source == "geo_record"

    def test_geo_metadata_prepended_when_present(self):
        text, _, _ = _resolve_geo_fulltext(GEO_PAPER_NO_PMIDS)
        assert _GEO_METADATA in text

    def test_falls_back_to_abstract_when_no_metadata(self):
        paper = {**GEO_PAPER_NO_METADATA, "pmids": []}
        text, source, _ = _resolve_geo_fulltext(paper)
        assert text == "GEO metadata summary text."
        assert source == "geo_record"


# ---------------------------------------------------------------------------
# pipeline.run() with optional criterion / fields
# ---------------------------------------------------------------------------

class TestPipelineRunOptionalArgs:
    """Tests for run() when criterion and/or fields_description are None."""

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.resolve_fulltext", side_effect=lambda p, *a, **kw: (p.get("abstract", ""), "abstract", {}))
    @patch("biolit.pipeline.fetch_record")
    def test_no_criterion_all_records_pass_screening(self, mock_fetch, mock_resolve, mock_citations, tmp_path):
        """When criterion=None, all records pass through to the output CSV without screening."""
        mock_fetch.side_effect = [FAKE_PAPER_1, FAKE_PAPER_2]
        mock_citations.return_value = None
        # schema build + two extract calls; no screen calls expected
        schema_resp = '{"methodology": "method", "summary": "summary"}'
        extract_1 = '{"methodology": "GWAS", "summary": "Study 1."}'
        extract_2 = '{"methodology": "scRNA-seq", "summary": "Study 2."}'
        client = FakeLLMClient([schema_resp, extract_1, extract_2])
        output = tmp_path / "results.csv"

        run(
            client=client,
            ids=["41795042", "41792186"],
            criterion=None,
            fields_description="methodology, summary",
            output_path=str(output),
        )

        csv_path = _find_csv(tmp_path)
        assert csv_path is not None, "CSV should be written for all records when criterion=None"
        rows = list(csv.DictReader(csv_path.open()))
        assert len(rows) == 2, "Both records should appear in the CSV"
        pmids = {r["pmid"] for r in rows}
        assert pmids == {"41795042", "41792186"}

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.resolve_fulltext", side_effect=lambda p, *a, **kw: (p.get("abstract", ""), "abstract", {}))
    @patch("biolit.pipeline.fetch_record")
    def test_no_fields_writes_metadata_only_rows(self, mock_fetch, mock_resolve, mock_citations, tmp_path):
        """When fields_description=None, metadata-only rows are written without calling extract_fields."""
        mock_fetch.side_effect = [FAKE_PAPER_1, FAKE_PAPER_2]
        mock_citations.return_value = 5
        # With no fields, no schema build and no extract calls; screening still happens
        screen_1 = '{"relevant": true, "reason": "Relevant."}'
        screen_2 = '{"relevant": true, "reason": "Also relevant."}'
        client = FakeLLMClient([screen_1, screen_2])
        output = tmp_path / "results.csv"

        run(
            client=client,
            ids=["41795042", "41792186"],
            criterion="Is this about schizophrenia?",
            fields_description=None,
            output_path=str(output),
        )

        csv_path = _find_csv(tmp_path)
        assert csv_path is not None, "CSV should be written even when fields_description=None"
        rows = list(csv.DictReader(csv_path.open()))
        assert len(rows) == 2
        # Metadata columns must be present
        required = {"title", "url", "pmid", "doi", "geo_accession", "text_source", "citation_count"}
        assert required.issubset(set(csv.DictReader(csv_path.open()).fieldnames))
        # No LLM-extracted columns beyond the metadata set
        extra_cols = set(csv.DictReader(csv_path.open()).fieldnames) - required - {"linked_pmids", "authors"}
        assert extra_cols == set(), f"Unexpected extra columns: {extra_cols}"

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.resolve_fulltext", side_effect=lambda p, *a, **kw: (p.get("abstract", ""), "abstract", {}))
    @patch("biolit.pipeline.fetch_record")
    def test_no_criterion_no_fields_writes_metadata_for_all(self, mock_fetch, mock_resolve, mock_citations, tmp_path):
        """When both criterion and fields_description are None, metadata rows for all records are written."""
        mock_fetch.side_effect = [FAKE_PAPER_1, FAKE_PAPER_2]
        mock_citations.return_value = None
        # No LLM calls expected at all
        client = FakeLLMClient([])
        output = tmp_path / "results.csv"

        run(
            client=client,
            ids=["41795042", "41792186"],
            criterion=None,
            fields_description=None,
            output_path=str(output),
        )

        csv_path = _find_csv(tmp_path)
        assert csv_path is not None
        rows = list(csv.DictReader(csv_path.open()))
        assert len(rows) == 2
        assert client.calls == [], "No LLM calls should be made when both criterion and fields are None"

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.resolve_fulltext", side_effect=lambda p, *a, **kw: (p.get("abstract", ""), "abstract", {}))
    @patch("biolit.pipeline.fetch_record")
    def test_criterion_given_but_no_fields_writes_metadata_for_relevant(self, mock_fetch, mock_resolve, mock_citations, tmp_path):
        """When criterion is given but fields_description=None, passing records get metadata-only rows."""
        mock_fetch.side_effect = [FAKE_PAPER_1, FAKE_PAPER_2]
        mock_citations.return_value = None
        # Paper 1 relevant, Paper 2 not relevant; no extract call expected
        screen_1 = '{"relevant": true, "reason": "Relevant."}'
        screen_2 = '{"relevant": false, "reason": "Not relevant."}'
        client = FakeLLMClient([screen_1, screen_2])
        output = tmp_path / "results.csv"

        run(
            client=client,
            ids=["41795042", "41792186"],
            criterion="Is this about schizophrenia?",
            fields_description=None,
            output_path=str(output),
        )

        csv_path = _find_csv(tmp_path)
        assert csv_path is not None
        rows = list(csv.DictReader(csv_path.open()))
        assert len(rows) == 1, "Only the relevant paper should appear"
        assert rows[0]["pmid"] == "41795042"
        # Only 2 LLM calls: two screen calls, no schema build or extract
        assert len(client.calls) == 2

    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.resolve_fulltext", side_effect=lambda p, *a, **kw: (p.get("abstract", ""), "abstract", {}))
    @patch("biolit.pipeline.fetch_record")
    def test_metadata_row_has_correct_values(self, mock_fetch, mock_resolve, mock_citations, tmp_path):
        """Metadata-only rows have correct title, url, pmid, doi, text_source values."""
        mock_fetch.return_value = FAKE_PAPER_1
        mock_citations.return_value = 7
        client = FakeLLMClient([])
        output = tmp_path / "results.csv"

        run(
            client=client,
            ids=["41795042"],
            criterion=None,
            fields_description=None,
            output_path=str(output),
        )

        csv_path = _find_csv(tmp_path)
        assert csv_path is not None
        rows = list(csv.DictReader(csv_path.open()))
        assert len(rows) == 1
        row = rows[0]
        assert row["pmid"] == FAKE_PAPER_1["pmid"]
        assert row["doi"] == FAKE_PAPER_1["doi"]
        assert row["title"] == FAKE_PAPER_1["title"]
        assert row["url"] == FAKE_PAPER_1["url"]
        assert row["text_source"] == "abstract"
        assert row["citation_count"] == "7"


# ---------------------------------------------------------------------------
# Tests for format_record_markdown and generate_markdown_summary
# ---------------------------------------------------------------------------

SAMPLE_SCHEMA = {
    "methodology": "experimental method used in the study",
    "summary": "brief plain-language summary of the paper",
}

SAMPLE_RECORD = {
    "title": "GWAS of schizophrenia",
    "url": "https://pubmed.ncbi.nlm.nih.gov/41795042/",
    "pmid": "41795042",
    "doi": "10.1038/s41588-026-01234-5",
    "geo_accession": None,
    "text_source": "pmc_fulltext",
    "citation_count": 42,
    "methodology": "GWAS",
    "summary": "Large genome-wide association study identifying 47 risk loci.",
}


class TestFormatRecordMarkdown:
    def test_normal_record_renders_header_and_llm_body(self):
        """Header metadata and LLM-generated body both appear in the output."""
        llm_body = "### Methodology\nUsed GWAS."
        client = FakeLLMClient([llm_body])
        result = format_record_markdown(client, SAMPLE_RECORD, SAMPLE_SCHEMA)
        assert "## GWAS of schizophrenia" in result
        assert "**PMID:** 41795042" in result
        assert llm_body in result

    def test_stub_record_shows_failure_note_without_llm_call(self):
        """Stub records render a failure note and make no LLM call."""
        client = FakeLLMClient([])
        stub = {"title": "Failed Paper", "_stub": True, "_stub_reason": "fetch error"}
        result = format_record_markdown(client, stub, SAMPLE_SCHEMA)
        assert len(client.calls) == 0
        assert "Failed Paper" in result
        assert "fetch error" in result

    def test_no_schema_returns_header_only_without_llm_call(self):
        """Metadata-only run (no schema) renders just the header, no LLM call."""
        client = FakeLLMClient([])
        result = format_record_markdown(client, SAMPLE_RECORD, None)
        assert len(client.calls) == 0
        assert "## GWAS of schizophrenia" in result


class TestGenerateMarkdownSummary:
    def test_renders_all_records_with_h1_header(self):
        """Output starts with H1, contains both record titles, stubs included without LLM call."""
        stub = {"title": "Broken Paper", "_stub": True, "_stub_reason": "network error"}
        client = FakeLLMClient(["body"])
        result = generate_markdown_summary(client, [SAMPLE_RECORD, stub], SAMPLE_SCHEMA)
        assert result.startswith("# Literature Search Results")
        assert "GWAS of schizophrenia" in result
        assert "Broken Paper" in result
        assert len(client.calls) == 1  # only non-stub calls LLM


# ---------------------------------------------------------------------------
# Tests for markdown=True wired into run()
# ---------------------------------------------------------------------------

class TestPipelineRunMarkdown:
    @patch("biolit.pipeline.get_citation_count")
    @patch("biolit.pipeline.resolve_fulltext", side_effect=lambda p, *a, **kw: (p.get("abstract", "text"), "abstract", {}))
    @patch("biolit.pipeline.fetch_record")
    def test_markdown_true_writes_md_with_stub_for_extraction_error(self, mock_fetch, mock_resolve, mock_citations, tmp_path):
        """markdown=True writes results.md; extraction failures appear as stubs."""
        mock_fetch.return_value = FAKE_PAPER_1
        mock_citations.return_value = 0
        client = FakeLLMClient(['{"methodology": "method"}', "NOT JSON"])

        run(
            client=client,
            ids=["41795042"],
            fields_description="methodology",
            output_path=str(tmp_path / "results.csv"),
            markdown=True,
        )

        md_files = list(tmp_path.rglob("results.md"))
        assert len(md_files) == 1
        content = md_files[0].read_text()
        assert "# Literature Search Results" in content
        assert "could not be fully processed" in content


# ---------------------------------------------------------------------------
# Tests for PubMed author parsing
# ---------------------------------------------------------------------------

PUBMED_XML_WITH_AUTHORS = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <Article>
        <ArticleTitle>A schizophrenia GWAS study</ArticleTitle>
        <Abstract><AbstractText>Abstract text.</AbstractText></Abstract>
        <AuthorList>
          <Author ValidYN="Y">
            <LastName>Smith</LastName>
            <ForeName>Jane</ForeName>
            <Initials>J</Initials>
          </Author>
          <Author ValidYN="Y">
            <LastName>Jones</LastName>
            <Initials>AB</Initials>
          </Author>
          <Author ValidYN="Y">
            <CollectiveName>SCHEMA Consortium</CollectiveName>
          </Author>
        </AuthorList>
      </Article>
      <MeshHeadingList/>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="doi">10.1038/test</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""


class TestPubmedAuthorParsing:
    @patch("biolit.fetchers.pubmed.time.sleep")
    @patch("biolit.fetchers.pubmed.requests.get")
    def test_parses_authors_from_xml(self, mock_get, mock_sleep):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.content = PUBMED_XML_WITH_AUTHORS
        mock_get.return_value = resp

        result = fetch_pubmed_metadata("99999999")
        assert result["authors"] == "Smith J, Jones AB, SCHEMA Consortium"

    @patch("biolit.fetchers.pubmed.time.sleep")
    @patch("biolit.fetchers.pubmed.requests.get")
    def test_authors_none_when_no_author_list(self, mock_get, mock_sleep):
        xml = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <Article>
        <ArticleTitle>No authors</ArticleTitle>
        <Abstract><AbstractText>Text.</AbstractText></Abstract>
      </Article>
      <MeshHeadingList/>
    </MedlineCitation>
    <PubmedData><ArticleIdList/></PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.content = xml
        mock_get.return_value = resp

        result = fetch_pubmed_metadata("99999999")
        assert result["authors"] is None

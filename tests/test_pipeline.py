"""Integration tests for the full pipeline, with all network and LLM calls mocked.

This lets the pipeline run end-to-end without credentials or internet access.
Replace the stub .eml fixture with your real alert file when you have one.
"""
import csv
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from biolit.llm.base import BaseLLMClient
from biolit.pipeline import run, run_geo, build_output_schema, screen_paper, extract_fields, screen_by_doi, resolve_fulltext

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
# Fake metadata returned by fetch_pubmed_metadata
# ---------------------------------------------------------------------------

FAKE_PAPER_1 = {
    "pmid": "41795042",
    "doi": "10.1038/s41588-026-01234-5",
    "title": "Genome-wide association study of schizophrenia in a European cohort",
    "abstract": "GWAS of schizophrenia in 130,000 individuals identified 47 loci.",
    "mesh_terms": ["Schizophrenia", "Genome-Wide Association Study"],
    "url": "https://pubmed.ncbi.nlm.nih.gov/41795042/",
    "fulltext_xml": None,
    "fulltext_pdf": None,
}

FAKE_PAPER_2 = {
    "pmid": "41792186",
    "doi": "10.1016/j.biopsych.2026.01.005",
    "title": "Transcriptomic profiling of prefrontal cortex in schizophrenia",
    "abstract": "scRNA-seq of 200,000 nuclei from schizophrenia patients and controls.",
    "mesh_terms": ["Schizophrenia", "Transcriptome", "Prefrontal Cortex"],
    "url": "https://pubmed.ncbi.nlm.nih.gov/41792186/",
    "fulltext_xml": None,
    "fulltext_pdf": None,
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


# ---------------------------------------------------------------------------
# Full end-to-end pipeline integration test
# ---------------------------------------------------------------------------

def _find_csv(tmp_path: pathlib.Path) -> pathlib.Path | None:
    """Return the results.csv written inside a run_* subdirectory, or None."""
    matches = list(tmp_path.rglob("results.csv"))
    return matches[0] if matches else None


class TestPipelineRun:
    """Run the complete pipeline with mocked network calls and a fake LLM."""

    def _make_client(self, paper1_relevant=True, paper2_relevant=False):
        """Build a FakeLLMClient with canned responses for a 2-paper run."""
        schema_resp = '{"methodology": "experimental method", "summary": "brief summary"}'
        screen_1 = json.dumps({"relevant": paper1_relevant, "reason": "Matches criterion."})
        screen_2 = json.dumps({"relevant": paper2_relevant, "reason": "Does not match."})
        extract_1 = '{"methodology": "GWAS", "summary": "Large GWAS of schizophrenia."}'
        # If paper2 is relevant too, add extraction response
        responses = [schema_resp, screen_1]
        if paper1_relevant:
            responses.append(extract_1)
        responses.append(screen_2)
        if paper2_relevant:
            responses.append('{"methodology": "scRNA-seq", "summary": "Transcriptomics study."}')
        return FakeLLMClient(responses)

    @patch("biolit.pipeline.fetch_pubmed_metadata")
    def test_one_relevant_paper_writes_csv(self, mock_fetch, eml_path, tmp_path):
        mock_fetch.side_effect = [FAKE_PAPER_1, FAKE_PAPER_2]
        client = self._make_client(paper1_relevant=True, paper2_relevant=False)
        output = tmp_path / "results.csv"

        run(
            client=client,
            pmids=["41795042", "41792186"],
            criterion="Is this about schizophrenia genomics?",
            fields_description="methodology, summary",
            output_path=str(output),
            fulltext=False,
        )

        csv_path = _find_csv(tmp_path)
        assert csv_path is not None, "CSV output file should be created"
        rows = list(csv.DictReader(csv_path.open()))
        assert len(rows) == 1
        assert rows[0]["pmid"] == "41795042"
        assert rows[0]["methodology"] == "GWAS"
        assert rows[0]["text_source"] == "abstract"

    @patch("biolit.pipeline.fetch_pubmed_metadata")
    def test_no_relevant_papers_no_csv(self, mock_fetch, eml_path, tmp_path):
        mock_fetch.side_effect = [FAKE_PAPER_1, FAKE_PAPER_2]
        schema_resp = '{"methodology": "method"}'
        screen_resp = '{"relevant": false, "reason": "Nope."}'
        client = FakeLLMClient([schema_resp, screen_resp, screen_resp])
        output = tmp_path / "results.csv"

        run(
            client=client,
            pmids=["41795042", "41792186"],
            criterion="Unrelated criterion",
            fields_description="methodology",
            output_path=str(output),
            fulltext=False,
        )

        assert _find_csv(tmp_path) is None, "CSV should not be created when no papers are relevant"

    @patch("biolit.pipeline.fetch_pubmed_metadata")
    def test_fetch_error_is_skipped_gracefully(self, mock_fetch, eml_path, tmp_path):
        mock_fetch.side_effect = [Exception("Network error"), FAKE_PAPER_2]
        schema_resp = '{"methodology": "method"}'
        screen_resp = '{"relevant": false, "reason": "Nope."}'
        client = FakeLLMClient([schema_resp, screen_resp])
        output = tmp_path / "results.csv"

        # Should not raise even though the first fetch fails
        run(
            client=client,
            pmids=["41795042", "41792186"],
            criterion="Any",
            fields_description="methodology",
            output_path=str(output),
            fulltext=False,
        )

    @patch("biolit.pipeline.fetch_pmc_fulltext")
    @patch("biolit.pipeline.fetch_pubmed_metadata")
    def test_fulltext_pmc_used_when_available(self, mock_fetch, mock_pmc, eml_path, tmp_path, sample_jats_xml):
        mock_fetch.side_effect = [FAKE_PAPER_1, FAKE_PAPER_2]
        # First paper has PMC full text; second does not
        mock_pmc.side_effect = [sample_jats_xml, None]

        schema_resp = '{"methodology": "method", "summary": "summary"}'
        screen_1 = '{"relevant": true, "reason": "Matches."}'
        extract_1 = '{"methodology": "GWAS", "summary": "Full text study."}'
        screen_2 = '{"relevant": false, "reason": "No."}'
        client = FakeLLMClient([schema_resp, screen_1, extract_1, screen_2])
        output = tmp_path / "results.csv"

        run(
            client=client,
            pmids=["41795042", "41792186"],
            criterion="Is this about schizophrenia?",
            fields_description="methodology, summary",
            output_path=str(output),
            fulltext=True,
        )

        csv_path = _find_csv(tmp_path)
        assert csv_path is not None
        rows = list(csv.DictReader(csv_path.open()))
        assert len(rows) == 1
        assert rows[0]["text_source"] == "pmc_fulltext"

    @patch("biolit.pipeline.fetch_pubmed_metadata")
    def test_csv_contains_required_columns(self, mock_fetch, eml_path, tmp_path):
        mock_fetch.side_effect = [FAKE_PAPER_1, FAKE_PAPER_2]
        client = self._make_client(paper1_relevant=True, paper2_relevant=False)
        output = tmp_path / "results.csv"

        run(
            client=client,
            pmids=["41795042", "41792186"],
            criterion="Any",
            fields_description="methodology, summary",
            output_path=str(output),
            fulltext=False,
        )

        csv_path = _find_csv(tmp_path)
        assert csv_path is not None
        reader = csv.DictReader(csv_path.open())
        assert set(["title", "url", "pmid", "text_source"]).issubset(set(reader.fieldnames))


# ---------------------------------------------------------------------------
# run_geo tests
# ---------------------------------------------------------------------------

FAKE_GEO_RECORD = {
    "pmid": "31123247",
    "accession": "GSE53987",
    "doi": None,
    "title": "Microarray profiling of PFC, HPC and STR from subjects with schizophrenia",
    "abstract": "Summary: Gene expression profiling of postmortem brain tissue.\n\nOverall design: Matched cases and controls.",
    "mesh_terms": ["Expression profiling by array", "Homo sapiens"],
    "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE53987",
    "pmids": ["31123247"],
    "text_source": "geo_record",
}


class TestRunGeo:
    def _make_client(self, relevant=True):
        schema_resp = '{"methodology": "experimental method", "summary": "brief summary"}'
        screen_resp = json.dumps({"relevant": relevant, "reason": "Matches." if relevant else "Does not match."})
        responses = [schema_resp, screen_resp]
        if relevant:
            responses.append('{"methodology": "Microarray", "summary": "Brain expression study."}')
        return FakeLLMClient(responses)

    @patch("biolit.pipeline.fetch_geo_record")
    def test_relevant_record_writes_csv(self, mock_fetch, tmp_path):
        mock_fetch.return_value = FAKE_GEO_RECORD
        client = self._make_client(relevant=True)
        output = tmp_path / "results.csv"

        run_geo(
            client=client,
            accessions=["GSE53987"],
            criterion="Is this a schizophrenia gene expression study?",
            fields_description="methodology, summary",
            output_path=str(output),
        )

        run_dir = next(tmp_path.iterdir())
        csv_path = run_dir / "results.csv"
        assert csv_path.exists()
        rows = list(csv.DictReader(csv_path.open()))
        assert len(rows) == 1
        assert rows[0]["geo_accession"] == "GSE53987"
        assert rows[0]["pmids"] == "31123247"
        assert rows[0]["text_source"] == "geo_record"

    @patch("biolit.pipeline.fetch_geo_record")
    def test_irrelevant_record_no_csv(self, mock_fetch, tmp_path):
        mock_fetch.return_value = FAKE_GEO_RECORD
        client = self._make_client(relevant=False)
        output = tmp_path / "results.csv"

        run_geo(
            client=client,
            accessions=["GSE53987"],
            criterion="Is this about Arabidopsis?",
            fields_description="methodology, summary",
            output_path=str(output),
        )

        run_dir = next(tmp_path.iterdir())
        assert not (run_dir / "results.csv").exists()

    @patch("biolit.pipeline.fetch_geo_record")
    def test_fetch_error_skipped_gracefully(self, mock_fetch, tmp_path):
        mock_fetch.side_effect = RuntimeError("GEO unavailable")
        client = self._make_client(relevant=True)
        output = tmp_path / "results.csv"

        run_geo(
            client=client,
            accessions=["GSE53987"],
            criterion="Any criterion",
            fields_description="methodology",
            output_path=str(output),
        )

    @patch("biolit.pipeline.fetch_geo_record")
    def test_pmid_column_not_duplicated(self, mock_fetch, tmp_path):
        mock_fetch.return_value = FAKE_GEO_RECORD
        client = self._make_client(relevant=True)
        output = tmp_path / "results.csv"

        run_geo(
            client=client,
            accessions=["GSE53987"],
            criterion="Any",
            fields_description="methodology, summary",
            output_path=str(output),
        )

        run_dir = next(tmp_path.iterdir())
        reader = csv.DictReader((run_dir / "results.csv").open())
        assert "pmid" not in reader.fieldnames
        assert "geo_accession" in reader.fieldnames
        assert "pmids" in reader.fieldnames


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
        # parse_pdf_sections needs to return something; patch it too
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

# Changelog

All notable changes to `biolit` are documented here.

## [0.1.7] — 2026-03-17

### Changed
- **Full-text retrieval is now always-on** — the `--fulltext` CLI flag has been removed. Every PubMed run now attempts full-text retrieval automatically (PMC → Europe PMC → preprint → Unpaywall → Semantic Scholar PDF → abstract fallback). The `fulltext` parameter has been removed from `screen_by_pmid()`, `run()`, and the MCP tools `screen_by_pmid` and `run_pipeline`.

## [0.1.6] — 2026-03-16

### Added
- **Citation counts** — `run()` and `run_geo()` now look up citation counts from the Semantic Scholar API after each extraction and write a `citation_count` column to the results CSV. Uses `PMID` lookup first, falls back to DOI. No API key required; set `SEMANTIC_SCHOLAR_API_KEY` for higher rate limits.
- **`get_citation_count(doi, pmid)`** in `biolit/fetchers/semantic_scholar.py` — returns citation count for a paper given either identifier; returns `None` if not found.

### Changed
- **`doi_to_pmid`** in `biolit/fetchers/pubmed.py` now uses PubMed esearch (`{doi}[doi]` field query) instead of the NCBI ID Converter. This resolves DOIs for Elsevier and other non-PMC journals that were previously unresolvable.

## [0.1.5] — 2026-03-16

### Fixed
- `test_mcp_server.py` now sets a dummy `ANTHROPIC_API_KEY` before importing `biolit.mcp_server`, fixing CI failures where the module-level LLM client initialisation raised `EnvironmentError` in environments without the key set

## [0.1.4] — 2026-03-16

### Added
- **Europe PMC fetcher** (`biolit/fetchers/europepmc.py`) — retrieves JATS XML from Europe PMC by PMID or DOI; used as step 2 in the full-text chain, after PMC JATS and before preprints
- **Semantic Scholar fetcher** (`biolit/fetchers/semantic_scholar.py`) — looks up open-access PDFs via the Semantic Scholar API (`get_s2_pdf_url`, `fetch_s2_pdf`); used as step 5 in the full-text chain; authenticates via `SEMANTIC_SCHOLAR_API_KEY` env var if set
- **DOI support in `biolit/fetchers/pubmed.py`** — added `_idconv_lookup`, `doi_to_pmid`, and `doi_to_pmcid` helpers using the NCBI ID Converter API
- **`fetch_preprint_metadata`** in `biolit/fetchers/preprints.py` — returns title + abstract from the bioRxiv/medRxiv API when JATS XML is blocked by Cloudflare (last-resort fallback for preprint DOIs)
- **`10.64898/` DOI prefix** recognised as a preprint DOI in `_is_preprint_doi` (new bioRxiv prefix introduced in 2025)
- **`screen_by_doi`** pipeline function — screens a paper by DOI with its own fallback chain: preprint JATS XML → Europe PMC → Unpaywall PDF → Semantic Scholar PDF → preprint abstract API
- **DOI support in `biolit screen`** — `--doi` flag screens a paper by DOI without needing a PMID
- **DOI file input** — positional file input whose first line starts with `10.` is auto-detected as a DOI list; DOIs are resolved to PMIDs via NCBI and unresolvable ones are sent through `screen_by_doi` directly
- **`--dois` flag** — inline comma-separated DOIs as an alternative to `--pmids`
- **Three new MCP tools:**
  - `resolve_doi` — resolves a DOI to PMID + PMCID via the NCBI ID Converter
  - `screen_by_doi` — fetches and screens a paper by DOI in one call
  - `lookup_s2_pdf` — checks whether Semantic Scholar has an open-access PDF for a DOI

### Changed
- **Full-text retrieval chain** extended from 4 to 6 steps: PMC JATS XML → Europe PMC JATS XML → Preprint XML → Unpaywall PDF → Semantic Scholar PDF → Abstract fallback
- `fetch_fulltext` MCP tool docstring updated to reflect the expanded source chain

## [0.1.2] — 2026-03-15

### Added
- **MCP server** (`biolit-mcp`) — exposes biolit's pipeline as 8 MCP tools consumable by any MCP-compatible client (Claude Desktop, Claude CLI, OpenAI Agents SDK, etc.)
  - `search_pubmed` — fetch PubMed metadata by PMID
  - `fetch_geo_record` — fetch and parse a GEO record by accession
  - `fetch_fulltext` — retrieve full text for a PMID (PMC → preprint → Unpaywall → abstract)
  - `screen_paper` — LLM relevance screen given pre-fetched text
  - `screen_by_pmid` — fetch + screen a PubMed paper in one call
  - `screen_by_geo` — fetch + screen a GEO record in one call
  - `extract_fields` — structured field extraction given pre-fetched text
  - `read_pmids_from_eml` — parse PMIDs from a PubMed alert `.eml` file
- **`screen_by_pmid` and `screen_by_geo`** added to `pipeline.py` as public library functions (importable without the MCP layer)
- `mcp[cli]` dependency added to `pyproject.toml`
- **`biolit screen` subcommand** — quickly screen a single PMID or GEO accession for relevance without running the full extraction pipeline; supports `--default`, `--criterion`, and `--fulltext`
- **`run_pipeline` and `run_geo_pipeline` MCP tools** — batch screen + extract pipeline exposed as MCP tools, equivalent to running `biolit` from the CLI
- Updated README with `biolit screen` usage and reorganised MCP tools table into batch, single-record, and low-level groups

## [0.1.1] — 2026-03-15

### Documentation
- Improved `--help` output: updated description, added usage examples and environment variable reference
- Added `CHANGELOG.md`
- Linked changelog from `pyproject.toml`

## [0.1.0] — 2026-03-15

Initial PyPI release. Renamed package from `pubmed_screener` to `biolit`.

### Features

- **PubMed pipeline** — accepts PubMed alert emails (`.eml`), plain PMID list files, or inline `--pmids` flag
- **GEO pipeline** — accepts GEO accession list files (GSE/GDS/GSM/GPL) or inline `--accessions` flag; fetches MINiML XML and runs the same LLM screen + extract pipeline
- **LLM screening** — two-call pipeline: screen for relevance (yes/no), then extract structured fields
- **Configurable criterion and fields** — pass `--criterion` and `--fields` as flags, use `--default` for schizophrenia genomics defaults, or be prompted interactively
- **Full-text retrieval** (`--fulltext`) — tries PMC JATS XML → preprint XML (bioRxiv/medRxiv) → Unpaywall PDF → abstract fallback
- **Section filtering** (`--sections`) — limit which full-text sections are sent to the LLM to control token usage
- **Multiple LLM providers** — Anthropic (default), OpenAI, and local Ollama; configurable via `--provider`/`--model` or `LLM_PROVIDER`/`LLM_MODEL` env vars
- **Timestamped output** — each run writes to `run_YYYYMMDD_HHMMSS/results.csv` with per-record `artifacts/` folders

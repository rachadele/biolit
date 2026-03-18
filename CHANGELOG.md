# Changelog

All notable changes to `biolit` are documented here.

## [0.1.14] — 2026-03-18

### Added
- **`input_file` config key** — `biolit/config.py` now accepts `input_file` as a valid JSON config key. Setting it to a path (`.eml` or identifier list) removes the need for a positional CLI argument when using `--config`. Priority: `--ids` CLI flag > `ids` in config > `input_file` in config > positional arg.

## [0.1.13] — 2026-03-18

### Added
- **JSON config file support** (`biolit/config.py`) — `load_config(path)` reads a JSON file with keys `ids`, `criterion`, `fields`, `provider`, `model`, `sections`, `max_chars`, `unpaywall_email`, `output`. Unknown keys raise `ValueError`. See `config.example.json` for a template.
- **`--config FILE` CLI flag** — load any subset of run parameters (including `ids`) from a JSON file; explicit CLI flags take precedence. Priority: CLI flag > config file > env var > hardcoded default. A config with `ids` satisfies the input requirement — no positional argument or `--ids` needed.
- **`config_path` parameter for `run_pipeline` MCP tool** — pass a path to a JSON config file to drive the entire run; explicit tool arguments override config values. `ids` in the config is used when the `ids` arg is empty.

### Changed
- **Screening and extraction are now optional** — `--criterion` skips the LLM screening step when omitted (all records pass through). Interactive prompts that previously forced input have been removed.
- **Default fields always applied** — `--fields` defaults to `methodology, sample_type, causal_claims, summary`; pass `--fields` to override. When `fields_description=None` is passed directly to `pipeline.run()`, metadata-only rows are written instead.
- **`genetics_claims` removed from default fields** — dropped from `DEFAULT_FIELDS`; include it explicitly via `--fields` if needed.
- **`run_pipeline` MCP tool — all args now optional** — `ids`, `criterion`, `fields`, `output_path`, and `unpaywall_email` all default to empty string; `criterion` empty skips screening, `fields` empty uses `DEFAULT_FIELDS`.
- **`pipeline.run()` signature** — `criterion`, `fields_description`, and `output_path` are now optional keyword arguments (default `None`, `None`, `"results.csv"`).

## [0.1.12] — 2026-03-17

### Added
- **`fetch_geo_fulltext` MCP tool** — retrieves full text for a GEO accession via its linked PMIDs (same chain as `fetch_fulltext`); returns `{"text": "...", "source": "geo_linked_fulltext" | "geo_linked_abstract" | "geo_record"}`. Useful for custom MCP workflows that call tools individually rather than using `run_pipeline`.

### Changed
- **`run_pipeline` MCP tool docstring** — corrected description of GEO full-text behaviour (now accurately reflects that linked PMIDs are tried before falling back to GEO metadata).

## [0.1.11] — 2026-03-17

### Added
- **Full-text retrieval for GEO records** — GEO accessions now attempt full-text retrieval via their linked PMIDs (using the same PMC → Europe PMC → Unpaywall → S2 chain as regular papers). `text_source` is set to `geo_linked_fulltext` when a full text is found, `geo_linked_abstract` when only an abstract is available, or `geo_record` when no linked paper is accessible (previous behaviour).

## [0.1.10] — 2026-03-17

### Changed
- **MCP server — unified entry point**: removed `screen_by_pmid`, `screen_by_doi`, and `screen_by_geo` MCP tools. Use `run_pipeline` for all ID types (PMIDs, DOIs, GEO accessions, or any mix) — it handles auto-detection via `_detect_id_type`.
- **`run_pipeline` return value**: now returns `{"output_path": "..." | null, "relevant_count": N}` (was `{"output_path": "...", "id_count": N}`). `output_path` is `null` when no records pass screening.
- **`pipeline.run()` return type**: changed from `None` to `tuple[str | None, int]` — returns the CSV path and relevant record count. The CLI is unaffected.

### Fixed
- **Extraction truncation**: bumped `extract_fields` `max_tokens` from 500 → 1024. With 5+ output fields, 500 tokens was insufficient, causing truncated JSON that was silently dropped.
- **Screening robustness**: bumped `screen_paper` `max_tokens` from 150 → 256.
- **JSON parse resilience**: `parse_json_response` now falls back to searching for the first `{...}` block in the response if a direct parse fails, handling LLM preamble text that would previously cause silent record drops.

## [0.1.9] — 2026-03-17

### Fixed
- **MCP server performance** — all `print()` calls in `pipeline.py` now write to `sys.stderr` instead of `stdout`. Writing progress output to `stdout` was corrupting the MCP stdio JSON-RPC stream, causing the server to stall waiting for responses that never arrived. The CLI is unaffected.

## [0.1.8] — 2026-03-17

### Added
- **Unified pipeline** — `run()` now accepts a mixed list of PMIDs, DOIs, and GEO accessions via a single `ids` parameter. Each identifier is auto-detected by format and routed to the appropriate fetcher (`fetch_pubmed_metadata`, `fetch_geo_record`, or `fetch_preprint_metadata`).
- **`fetch_record(id_str)`** in `pipeline.py` — normalises any identifier into a paper dict with `pmid`, `doi`, and `geo_accession` keys always present.
- **`_detect_id_type(id_str)`** in `pipeline.py` — classifies an identifier as `pmid`, `doi`, or `geo`.
- **Native preprint/medRxiv support** — DOIs that cannot be resolved to a PMID (e.g. medRxiv papers not yet in PubMed) now flow through the full pipeline: metadata from the bioRxiv/medRxiv API, full-text from the preprint chain. No records are skipped due to a missing PMID.
- **Unified `--ids` CLI flag** — replaces `--pmids`, `--dois`, and `--accessions`; accepts any mix of identifier types.
- **Unified `run_pipeline` MCP tool** — replaces `run_pipeline` (PMID-only) and `run_geo_pipeline`; accepts the same mixed `ids` string.
- **`geo_accession` column in all CSVs** — present in every output row (null for non-GEO records), alongside `pmid` and `doi`.
- **`linked_pmids` column** — GEO records include all associated PubMed IDs in a separate column.
- **`resolve_fulltext()` handles pmid=None** — PMC step is skipped when no PMID is available; Europe PMC and preprint steps proceed via DOI.
- **`tests/test_integration.py`** — real network + LLM test for the medRxiv DOI `10.1101/2025.03.17.25324098`; auto-skipped when `ANTHROPIC_API_KEY` is unset.

### Removed
- **`run_geo()`** — superseded by the unified `run()`.
- **`--pmids`, `--dois`, `--accessions` CLI flags** — replaced by `--ids`.
- **`run_geo_pipeline` MCP tool** — replaced by the unified `run_pipeline`.

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

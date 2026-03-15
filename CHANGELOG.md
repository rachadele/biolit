# Changelog

All notable changes to `biolit` are documented here.

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

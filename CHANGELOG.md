# Changelog

All notable changes to `biolit` are documented here.

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

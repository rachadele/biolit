# Changelog

All notable changes to `biolit` are documented here.

## [0.1.29] — 2026-05-01

### Added
- **Configurable provider for `biolit-mcp`** — the MCP server now accepts `--provider` and `--model` CLI flags, so the LLM can be selected from `.mcp.json` / `claude_desktop_config.json` without an `env` block:
  ```json
  {
    "mcpServers": {
      "biolit": {
        "command": "biolit-mcp",
        "args": ["--provider", "openai", "--model", "gpt-4o-mini"]
      }
    }
  }
  ```
  Flags take precedence over `LLM_PROVIDER` / `LLM_MODEL` env vars; both still work.

### Changed
- **Keychain is now preferred over env vars for API key resolution** — `biolit.llm.base.resolve_api_key()` checks the macOS keychain first, then falls back to the env var (previously env-first). This prevents a stale value in `.env` (loaded with `override=True`) from masking a working keychain entry. On non-darwin platforms the env var remains the only source.
- **`biolit-mcp` lazily initializes its LLM client** on the first LLM-touching tool call. The server now starts (and non-LLM tools like `fetch_pubmed_metadata` still work) when the configured provider's API key isn't available — failures surface only when an LLM-using tool is actually invoked.

## [0.1.28] — 2026-05-01

### Added
- **macOS keychain fallback for API keys** — `AnthropicClient` and `OpenAIClient` now consult the macOS keychain when `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` is not set in the environment. The lookup uses the `security` command and matches by service name only (no account required), so an entry stored the conventional way — `security add-generic-password -s ANTHROPIC_API_KEY -w` — works as-is. Env vars still win when present, so `.env` workflows are unchanged. New helper `biolit.llm.base.resolve_api_key(env_var)` encapsulates the lookup. No new Python dependencies (uses macOS's built-in `security` binary).

## [0.1.27] — 2026-04-30

### Fixed
- **Test suite isolation** — `tests/conftest.py` now clears the fetcher registry at session start so any fetchers auto-registered from the developer's `.env` (`BIOLIT_BIBTEX`, `BIOLIT_LOCAL_PDF_DIR`, `ZOTERO_API_KEY`, …) don't contaminate pipeline tests. Previously, a developer with `BIOLIT_BIBTEX` set could see `test_pipeline.py` failures when a test DOI happened to be in their `.bib` library.

### Added
- **BibTeX-backed reference fetcher** (`biolit/fetchers/bibtex.py`) — looks up papers in a `.bib` export by DOI, PMID, or citekey, reads the path from each entry's `file = {...}` field, and parses the PDF directly. Self-registers when `BIOLIT_BIBTEX` points at a `.bib` file (priority 2.0, before `local_pdf` and `zotero`); priority is overridable via `BIOLIT_BIBTEX_PRIORITY`. Fills the gap left by the Zotero web API's q-search not indexing the structured `DOI` field — for users who maintain a Better-BibTeX (or equivalent) export, lookups become offline, instant, and exact instead of relying on the Zotero search index. Supports both BBT semicolon-separated `file` lists and the classic JabRef `description:path:type` triple format. Re-parses automatically when the source `.bib` file's mtime changes.

### Changed
- **`local_pdf` indexing is now incremental by default** — re-running `python -m biolit.fetchers.local_pdf --dir <path>` reuses prior entries whose `(mtime, size)` is unchanged, so a re-run on an unchanged 10k-PDF library now costs a stat per file rather than a full pdfminer pass over every PDF. New PDFs are picked up; deleted PDFs are dropped; modified PDFs are re-extracted. The CLI no longer refuses to run when an index already exists at the target path — pass `--rebuild` to force a full re-extraction (e.g. after a pdfminer upgrade or if you suspect cache corruption).
- **`local_pdf` index schema bumped to v2** — adds an `entries: {<path>: {doi, mtime, size}}` block alongside the existing `doi_to_path`. v1 indexes (no `entries` block) are detected and trigger a one-shot full rebuild, after which subsequent runs are incremental. The on-disk format is written atomically (`.tmp` + rename) so a Ctrl-C never leaves a half-written index behind.

## [0.1.26] — 2026-04-30

### Added
- **Custom fetcher hook registry** (`biolit/fetchers/_hooks.py`) — `register_fetcher(fn, priority, name)` lets external code prepend extra full-text sources before the built-in PMC → Europe PMC → preprint → Unpaywall → Semantic Scholar chain. Fetchers receive a `FetchContext` and return a `FetchResult` (text + source label + raw artifact bytes) or `None`. Lower priority runs earlier; exceptions are logged to stderr and the next fetcher is tried.
- **Zotero reference fetcher** (`biolit/fetchers/zotero.py`) — looks up papers in a Zotero library by DOI then PMID, downloads attached PDFs, parses them. Self-registers when `ZOTERO_API_KEY` + (`ZOTERO_USER_ID` or `ZOTERO_GROUP_ID`) are set. Resolves attachment search hits to their parent items so DOIs match correctly. Falls back to reading `$ZOTERO_DATA_DIR/storage/<key>/<filename>` (default `~/Zotero`) when the Zotero `/file` endpoint returns non-200 (covers `linked_file` attachments and unsynced imported attachments).
- **Local-PDF reference fetcher** (`biolit/fetchers/local_pdf.py`) — DOI-keyed lookup against a pre-built JSON index. Build the index with `python -m biolit.fetchers.local_pdf --dir <path>`; the fetcher self-registers when `BIOLIT_LOCAL_PDF_DIR` is set. DOIs are extracted from each PDF's `/Info` dict and (failing that) its first-page text. Index lives at `$XDG_CACHE_HOME/biolit/local_pdf_index_<hash>.json`.

### Changed
- **`load_dotenv(override=True)`** — `cli.py`, `mcp_server.py`, and `tests/conftest.py` now pass `override=True` so values in `.env` win over a stale shell env var. Previously, an old key exported in the shell would silently shadow the value in `.env`. Editing `.env` is now enough; no need to re-source the shell or restart the terminal.

## [0.1.25] — 2026-03-31

### Fixed
- **MCP registry ownership marker** — added plain-text `mcp-name` line to README alongside HTML comment to satisfy registry validation.

## [0.1.24] — 2026-03-31

### Added
- **MCP registry listing** — added `server.json` for publishing `biolit-mcp` to the official MCP registry (`io.github.rachadele/biolit`). Added ownership marker to README.

## [0.1.23] — 2026-03-29

### Added
- **`max_tokens` parameter on MCP `run_pipeline`** — exposes the input text token cap (default 12,500) via the MCP tool, consistent with the CLI `--max-tokens` flag and `"max_tokens"` config key. Pass `0` to use the default.

## [0.1.22] — 2026-03-26

### Added
- **`markdown_max_tokens` parameter** — controls the token budget for each per-record LLM call during markdown rendering. Configurable via `--markdown-max-tokens` CLI flag, `"markdown_max_tokens"` config file key, `markdown_max_tokens` arg on `run()`, and `markdown_max_tokens` arg on the MCP `run_pipeline` tool. Default remains 1024.
- **`extraction_max_tokens` parameter** — configurable output token budget for the field extraction LLM call (default 4096, up from the previous hardcoded 1024). Fixes JSON truncation errors on papers with verbose multi-field schemas. Configurable via `--extraction-max-tokens` CLI flag, `"extraction_max_tokens"` config file key, `extraction_max_tokens` arg on `run()`, and `extraction_max_tokens` arg on the MCP `run_pipeline` tool.

### Changed
- **`max_chars` → `max_tokens`** — the input text truncation parameter is now expressed in tokens (default 12,500) instead of characters (was 50,000). The effective limit is unchanged (~4 chars/token). CLI flag renamed to `--max-tokens`; config file key renamed to `"max_tokens"`.

## [0.1.21] — 2026-03-24

### Added
- **Author extraction** — `authors` column added to CSV and markdown output for all record types. PubMed records parse `<AuthorList>` from the E-utilities XML. Preprint records (bioRxiv/medRxiv) get authors from the API response. GEO records parse `<Contributor>` elements from MINiML XML; if none are present, authors are propagated from the first linked PubMed paper.

## [0.1.20] — 2026-03-24

### Added
- **Markdown export** (`--markdown` / `--md` CLI flag; `"markdown": true` in config; `markdown=True` in `run()` and `run_pipeline` MCP tool) — writes a `results.md` prose summary alongside the CSV. Each record gets an LLM-rendered section with `### field` subsections. Records that failed or were skipped appear as stub entries with a failure note, with no extra LLM call.
- **BibTeX and identifier file input for MCP `run_pipeline`** — added `bib_path` and `ids_file` parameters. `bib_path` accepts a path to a `.bib` file and extracts DOIs automatically; `ids_file` accepts a plain-text file of mixed identifiers (one per line). Precedence: `ids` > `bib_path` > `ids_file` > config `ids`.
- **`read_ids_file(path)`** in `biolit/utils.py` — reads a plain-text file of mixed identifiers (PMIDs, DOIs, GEO accessions), skipping blank lines and comments.
- **BibTeX input support (CLI)** — `.bib` files are now accepted as positional input to the CLI. DOIs are extracted from `doi = {...}` fields via `read_dois_from_bib()` in `biolit/utils.py`; entries without a DOI are silently skipped.
- **Stub entries for all skip points** — records that fail at fetch, not-found, no-content, screening error, or extraction error stages all produce stub markdown entries. Previously only extraction errors produced stubs. "Not relevant" (screening returned false) remains intentionally silent.
- **`format_record_markdown(client, record, output_schema)`** and **`generate_markdown_summary(client, records, output_schema)`** — new public functions in `biolit/pipeline.py` for rendering markdown from extracted records and stubs.
- **`markdown` config key** — added to `VALID_KEYS` in `biolit/config.py`.

### Changed
- **`DEFAULT_MAX_CHARS` increased to 50,000** — raised from 12,000 to 50,000 characters (~12,500 tokens) to improve full-text extraction quality by default.

## [0.1.19] — 2026-03-18

### Changed
- **GEO fetch now uses `targ=all`** — `fetch_geo_record` fetches the full MINiML XML (including `Platform` elements) instead of the series-only brief view. This enables extraction of platform GPL accession, title, and technology directly in Python without an LLM call.
- **Raw MINiML XML replaced with structured metadata text** — `_parse_miniml` now extracts `platforms`, `organisms`, and `sample_count` from the XML. A new `format_geo_metadata()` function produces a compact, human-readable block (accession, type, organism(s), platform(s), sample count, linked PMIDs, summary, overall design) stored as `geo_metadata_text` on the record. This replaces the raw `geo_xml` field that was previously appended to the LLM context.
- **GEO metadata prepended, not appended** — `_resolve_geo_fulltext` now prepends the structured metadata block before any linked publication text (separated by `--- Linked Publication ---`), so the LLM always sees GEO-specific fields regardless of text source. Raw XML is no longer passed to the LLM.

## [0.1.18] — 2026-03-18

### Changed
- **`fields` config key now accepts a JSON object** — in addition to a comma-separated string, `fields` in the JSON config file can be a `{field_name: description}` object. When a dict is provided, `build_output_schema` uses it directly and skips the schema-building LLM call. String `--fields` on the CLI continues to work as before.

## [0.1.17] — 2026-03-18

### Changed
- **GEO full-text now includes raw MINiML XML** — `fetch_geo_record` stores the decoded MINiML XML as `geo_xml` on the record dict. `_resolve_geo_fulltext` appends it under a `--- GEO MINiML XML ---` separator for all text sources (`geo_linked_fulltext`, `geo_linked_abstract`, `geo_record`), so the LLM always has access to structured GEO fields (platform GPL accession, organism, etc.) even when the main text comes from a linked publication.

## [0.1.16] — 2026-03-18

### Added
- **`--version` CLI flag** — `biolit --version` prints the installed package version.
- **`get_version` MCP tool** — returns the installed biolit package version from any MCP client.

## [0.1.15] — 2026-03-18

### Changed
- **Renamed MCP tool `search_pubmed` → `fetch_pubmed_metadata`** — the tool fetches metadata for a known PMID; the old name incorrectly implied keyword search functionality.

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

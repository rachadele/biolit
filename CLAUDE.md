# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Security

- **Never expose API keys** (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY`, `ZOTERO_API_KEY`, etc.). Read only via `os.environ` / `load_dotenv()`, or `biolit.llm.base.resolve_api_key()` for LLM keys (macOS keychain → env var fallback). Never echo a pasted key back.
- **Do not commit LLM-related files** — `.env`, transcripts, prompt/response dumps, run output dirs, cached model artifacts. Never `git add -A` / `git add .`; stage files by name.

## Project Overview

Claude-powered literature search agent for academic paper screening. Target user (Paul, a bioinformatics supervisor) gets weekly PubMed alerts and wants papers auto-screened, extracted, and summarized into a structured format.

**Package:** `biolit` · **Domain:** schizophrenia genomics · **Search keywords:** `schizophrenia genomics`

## Architecture

1. **Input:** `.eml` alert, `.bib` file, plain-text ID list, or `--ids`. Each ID auto-detected as PMID / DOI / GEO accession.
2. **Fetch metadata:** routed to `fetch_pubmed_metadata`, `fetch_geo_record`, or `fetch_preprint_metadata`.
3. **Fetch full text:** custom fetchers (`biolit.fetchers.register_fetcher()`, see `_hooks.py`) run first, then the built-in chain: PMC JATS → Europe PMC JATS → preprint XML → Unpaywall PDF → Semantic Scholar OA PDF → abstract fallback. GEO records run the same chain over each linked PMID, falling back to GEO metadata. A structured GEO metadata block is always prepended when available.
4. **(Optional) Screen** (`--criterion`): LLM yes/no relevance decision from abstract + MeSH (or GEO metadata).
5. **(Optional) Extract** (`--fields`): LLM pulls structured JSON fields; defaults to `DEFAULT_FIELDS` (`methodology, sample_type, causal_claims, summary`).
6. **Output:** timestamped run dir with `results.csv`, per-record `artifacts/`, optional `results.md` (`--markdown`).

Shared metadata columns: `title`, `authors`, `url`, `pmid`, `doi`, `geo_accession`, `text_source`, `citation_count`. GEO records add `linked_pmids`. `pmid`/`doi` are `null` when not applicable.

### LLM Calls (all optional)

1. **Screen** (`--criterion`) — omit to process all records.
2. **Schema-build** (`build_output_schema`) — skipped when `fields` is already a dict.
3. **Extract** (`--fields`) — omitting entirely (only via `pipeline.run(fields_description=None)`) writes metadata-only rows.
4. **Markdown render** (`--markdown`) — one call per extracted record; stub records get no call.

`--batch` (or `batch=True`) routes calls 1/3/4 through the provider's Batch API (~50% cheaper, blocks until done — several minutes per stage, 6h timeout). Anthropic + OpenAI only; falls back to sequential `chat()` on Ollama / custom `base_url`.

### Token Usage

- `--sections methods,results` limits sections sent to the LLM
- `--max-tokens` caps input text (default 12,500 tokens ≈ 50,000 chars)
- `--extraction-max-tokens` raises extraction output budget (default 4,096)
- `claude-haiku-4-5-20251001` for cheap bulk screening; combine with `--batch` for bulk weekly runs (not for one-off lookups)

## Commands

```bash
pip install -e .                          # editable install
cp .env.example .env                      # then set ANTHROPIC_API_KEY / OPENAI_API_KEY
pytest                                    # all tests
pytest tests/test_pipeline.py::test_screen_paper   # single test
biolit docs/alert.eml --default
biolit --ids 12345678,87654321,GSE53987,10.1101/2025.03.17.25324098 --default
biolit docs/alert.eml --config my_config.json
biolit-mcp                                # start MCP server
mcp dev biolit/mcp_server.py              # test MCP server interactively
```

## Custom Slash Commands

- `/release` — commit changes, bump patch version, tag, push (triggers PyPI workflow). Checks `CHANGELOG.md` first.
- `/update-docs` — update `CLAUDE.md`, `docs/PACKAGE_STRUCTURE.md`, `README.md`, `CHANGELOG.md`. Run before `/release`.
- `/write-tests` — write tests for functions modified per `git diff`, then run the suite.

## Key Files

- `biolit/config.py` — `load_config(path)`; `fields` may be a comma string or `{field: description}` dict (dict skips the schema-build LLM call)
- `biolit/cli.py` — CLI entry point; `.bib` input auto-detected via `read_dois_from_bib`
- `biolit/pipeline.py` — main pipeline. `run(..., batch=False)` dispatches to `_run_sequential_loop` / `_run_batch_loop`, sharing `_fetch_and_resolve_all`, `_persist_record_artifacts`, prompt builders. Also: `fetch_record`, `screen_paper`, `resolve_fulltext`, `_resolve_geo_fulltext`, `extract_fields`, `format_record_markdown`
- `biolit/llm/base.py` — `BaseLLMClient.chat_batch()` default loops `chat()`; `resolve_api_key()` checks macOS keychain before env var
- `biolit/llm/anthropic_client.py` / `openai_client.py` — native `chat_batch()` via Message Batches / Batches API
- `biolit/mcp_server.py` — MCP server; LLM client lazily built on first LLM-touching call; provider/model from `--provider`/`--model` flags or env vars
- `biolit/fetchers/pubmed.py`, `geo.py`, `preprints.py` — metadata parsers for each source
- `biolit/fetchers/_hooks.py` — fetcher registry (`register_fetcher`, priority-ordered, runs before the built-in chain)
- `biolit/fetchers/zotero.py`, `local_pdf.py`, `bibtex.py` — reference custom fetchers, auto-register from env vars (`ZOTERO_API_KEY`, `BIOLIT_LOCAL_PDF_DIR`, `BIOLIT_BIBTEX`)
- `biolit/parsers/jats.py` — JATS text extraction: `\n` at block boundaries (`sec`, `p`, `table`/`tr`/`td`, `fn`, …), none at inline boundaries (`sup`, `sub`, `xref`, …) so markup-split compound terms stay glued. `parse_jats_sections` keys top-level `<sec>` only (nested subsections dropped as duplicates) plus a `footnotes` key from `<fn>`
- `tests/` — pytest suite

## Testing Conventions

- **Fixtures** (`tests/conftest.py`): `sample_pubmed_metadata`, `sample_jats_xml`, `eml_path`, `test1_eml_path`
- Use a fake LLM client — never make real LLM calls in tests
- **HTTP mocking**: patch at the module level where `requests` is imported, not the shared `requests` module
- Patch `biolit.pipeline.fetch_record` (not `fetch_pubmed_metadata`) when testing `run()`. For GEO tests also patch `fetch_pubmed_metadata` to block `_resolve_geo_fulltext`'s network calls
- `tests/test_integration.py` makes live calls; auto-skipped when `ANTHROPIC_API_KEY` is unset

## Development Notes

- Rachel is building her first Claude agent — prefer simple, well-commented implementations over abstractions
- NCBI E-utilities: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`; Semantic Scholar: `https://api.semanticscholar.org/graph/v1`
- `cli.py`, `mcp_server.py`, `tests/conftest.py` all call `load_dotenv(override=True)` — editing `.env` is enough, no re-source needed
- Custom fetchers must register before the first `resolve_fulltext` call (handled by `maybe_autoload()`)
- The `schizophrenia genomics` alert query has known false positives and may need refinement

## Known Limitations

- bioRxiv/medRxiv JATS is often Cloudflare-blocked; falls back to title + abstract (`preprint_abstract`)
- Preprint DOIs with no PMID still work: `fetch_record()` falls back to `fetch_preprint_metadata()`
- Failed records are excluded from CSV but appear as markdown stubs with a failure note; "not relevant" screens are silently dropped (not errors)
- GEO records fall back linked-PMID fulltext → linked-PMID abstract → GEO metadata; the structured GEO block is always prepended when available

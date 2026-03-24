# Plan: Markdown Export Feature

## Goal
Add a `--markdown` / `--md` flag to the CLI (and corresponding `run_pipeline` MCP param) that generates a structured `.md` summary file alongside `results.csv` at the end of each pipeline run.

## Approach

Use a dedicated LLM prompt to render each extracted record into a formatted markdown section, rather than a simple template. This allows the LLM to:
- Write in natural prose rather than copying raw CSV values verbatim
- Handle missing fields gracefully (e.g. omit empty sections rather than writing "Not extracted")
- Adapt tone for different article types (e.g. commentary vs. systematic review vs. primary data)

## Implementation Steps

### 1. Add `format_record_markdown(client, record, output_schema)` to `pipeline.py`
- Takes the extracted record dict and the output schema (field descriptions) as context
- Sends a prompt asking the LLM to render the record as a markdown section with:
  - A header with title, URL, PMID, DOI, citation count, text source
  - One `###` subsection per extracted field, written in clean prose
  - Empty fields silently omitted
- Returns a markdown string

### 2. Add `generate_markdown_summary(client, results, output_schema)` to `pipeline.py`
- Iterates over all records and calls `format_record_markdown` for each
- Prepends a `# Literature Search Results` header
- Returns the full markdown document as a string

### 3. Wire into `run()` in `pipeline.py`
- Add `markdown: bool = False` parameter
- After writing `results.csv`, if `markdown=True`, call `generate_markdown_summary` and write to `<run_dir>/results.md` (or the output filename stem + `.md`)

### 4. Add `--markdown` flag to CLI (`cli.py`)
- Boolean flag, default False
- Pass through to `run()`

### 5. Add `markdown` param to MCP `run_pipeline` tool (`mcp_server.py`)
- Boolean, default False
- Pass through to `run()`

### 6. Add `markdown` key to supported config keys (`config.py`)

### 7. Write tests
- Unit test for `format_record_markdown` using FakeLLMClient
- Unit test for `generate_markdown_summary` with two records
- CLI test that `--markdown` flag is parsed and passed through

## Open Questions
- Should the markdown prompt be aware of `review_type` or article category to adapt its tone? (Probably yes — pass the full output schema so the LLM has field descriptions as context)
- Should empty/skipped records (extraction errors) still get a stub entry in the markdown, or be silently omitted? (Suggest: omit, consistent with current CSV behavior)
- Should `--markdown` be on by default eventually? Could replace the manual post-run conversion we're doing now.

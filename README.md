# biolit

LLM-assisted biomedical literature screening and structured extraction. Accepts PubMed alert emails and mixed lists of PMIDs, DOIs, and GEO accessions in any combination. Retrieves full text from PMC, Europe PMC, bioRxiv/medRxiv, Unpaywall, and Semantic Scholar. Supports multiple LLM providers and exposes all functionality as an MCP server.

## Setup

**Requirements:** Python 3.8+

Install from PyPI:

```bash
pip install biolit
```

Or install from source for development:

```bash
pip install -e .
```

Copy `.env.example` to `.env` and add your API key:

```bash
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY (or OPENAI_API_KEY)
```

## Usage

The tool accepts a PubMed alert email (`.eml`) or a plain-text file of identifiers, as well as inline identifiers via `--ids`. Identifiers can be PMIDs, DOIs, or GEO accessions — mixed lists are supported in a single run.

| Input | How to pass | Example |
|---|---|---|
| PubMed alert email | positional `.eml` file | `alert.eml` |
| BibTeX file | positional `.bib` file | `refs.bib` |
| Identifier file (mixed) | positional plain-text file, one per line | `identifiers.txt` |
| Inline identifiers | `--ids` flag, comma-separated | `--ids 41795042,GSE53987,10.1101/2025.03.17.25324098` |

Use `--default` to run with schizophrenia genomics defaults (no prompts):

```bash
biolit docs/alert.eml --default
biolit docs/pmids.txt --default
biolit docs/geo_accessions.txt --default
biolit --ids 41795042,41792186,GSE53987 --default
biolit --ids 10.1101/2025.03.17.25324098 --default
```

Or specify criterion and fields as flags:

```bash
biolit identifiers.txt \
  --criterion "Is this about treatment-resistant schizophrenia?" \
  --fields "methodology, sample_size, treatment, outcomes"
```

Add `--markdown` (or `--md`) to also write a prose `.md` summary alongside the CSV. Each record gets a markdown section with `### field` subsections; records that failed or were skipped appear as stub entries:

```bash
biolit refs.bib --config my_config.json --markdown
```

Or use a JSON config file to store reusable parameters (CLI flags take precedence). The config can include `ids` or `input_file` (path to an `.eml`, `.bib`, or identifier list), and `"markdown": true` to enable markdown output:

```bash
biolit alert.eml --config my_config.json
biolit refs.bib --config my_config.json   # DOIs extracted from .bib automatically
biolit --config my_config.json            # ids or input_file supplied by config
```

The `fields` key in a config file can be a comma-separated string or a JSON object mapping field names to extraction descriptions. When a string is used, an extra LLM call converts the field names into descriptions before extraction. When a dict is used, that call is skipped — the descriptions are passed directly to the model:

```json
{
  "fields": {
    "tf_name": "HGNC symbol of the transcription factor perturbed in this experiment",
    "organism": "scientific name of the organism used",
    "platform": "GPL accession of the microarray platform"
  }
}
```

Omit `--criterion` to skip screening (all records are extracted). Omit `--fields` to use the default fields (`methodology, sample_type, causal_claims, summary`):

```bash
# fetch + extract with defaults (no screening)
biolit alert.eml

# fetch + screen only, then extract with defaults
biolit alert.eml --criterion "Is this about treatment-resistant schizophrenia?"
```

### Single-record screening

Use `biolit screen` to quickly check one paper or GEO record for relevance without running the full extraction pipeline:

```bash
biolit screen --pmid 41627908 --default
biolit screen --accession GSE53987 --default
biolit screen --doi 10.64898/2026.02.16.706214 --default
biolit screen --pmid 41627908 --criterion "Is this about treatment-resistant schizophrenia?"
```

Output is a single line to stdout:

```
RELEVANT [abstract] — Paper uses GWAS to investigate schizophrenia risk loci.
```

### Mixed identifier lists

PMIDs, DOIs, and GEO accessions can be freely mixed in a file or via `--ids`. Each identifier is auto-detected by format:

- `41795042` → PMID (all digits)
- `10.1101/2025.03.17.25324098` → DOI (starts with `10.`)
- `GSE53987` → GEO accession (starts with `GSE`, `GDS`, `GSM`, or `GPL`)

```bash
biolit --ids 41795042,GSE53987,10.1101/2025.03.17.25324098 --default
```

GEO records additionally include a `linked_pmids` column. All record types share `pmid`, `doi`, and `geo_accession` columns (null when not applicable).

### Full-text retrieval

Full-text retrieval runs automatically for every PMID and DOI (including preprints). For GEO records, the pipeline attempts full-text retrieval via each linked PMID in order, falling back to the GEO record metadata if no linked paper has accessible full text. The pipeline tries each source in order:

1. PMC JATS XML (open access)
2. Europe PMC JATS XML (broader open-access coverage)
3. Preprint XML (bioRxiv / medRxiv)
4. Unpaywall PDF (requires `--unpaywall-email`)
5. Semantic Scholar open-access PDF
6. Abstract fallback

To enable Unpaywall (step 4), pass your email:

```bash
biolit alert.eml --default --unpaywall-email you@example.com
```

Limit which sections are sent to the LLM:

```bash
biolit alert.eml --default --sections methods,results
```

### LLM providers

The tool supports Anthropic (default), OpenAI, and local Ollama models:

```bash
# OpenAI
biolit pmids.txt --default --provider openai --model gpt-4o

# Ollama (local)
biolit pmids.txt --default --provider ollama --model llama3
```

You can also set `LLM_PROVIDER` and `LLM_MODEL` as environment variables.

## Output

Each run creates a timestamped directory (e.g. `run_20260313_142000/`) containing:

- `results.csv` — one row per relevant record
- `results.md` — prose markdown summary (written when `--markdown` or `"markdown": true` in config)
- `artifacts/<id>/` — per-record folder with the text sent to the LLM, metadata, and any retrieved full-text files

Records that fail at any pipeline stage (fetch error, not found, no content, screening or extraction error) are excluded from the CSV but appear in the markdown as stub entries with a failure note.

With default fields, the CSV columns are:

| Column | Description |
|---|---|
| `title` | Paper title |
| `authors` | Author list (comma-separated; parsed from PubMed XML, bioRxiv/medRxiv API, or GEO contributors) |
| `url` | Link to PubMed, GEO, or DOI |
| `pmid` | PubMed ID (null for unindexed preprints) |
| `doi` | DOI (null for GEO records) |
| `geo_accession` | GEO accession (null for non-GEO records) |
| `text_source` | Where the text came from (`abstract`, `pmc_fulltext`, `europepmc_fulltext`, `preprint_fulltext`, `unpaywall_pdf`, `s2_pdf`, `geo_linked_fulltext`, `geo_linked_abstract`, `geo_record`) |
| `citation_count` | Citation count from Semantic Scholar (null if not found) |
| `methodology` | General method (e.g. GWAS, scRNA-seq, proteomics) |
| `sample_type` | Tissue/sample type and origin |
| `causal_claims` | Statements about causes of schizophrenia inferred from the data |
| `summary` | 2-3 sentence plain-language summary for triage |

GEO records additionally include a `linked_pmids` column listing all associated PubMed IDs.

The CSV can be imported directly into Google Sheets (File → Import).

## MCP server

`biolit` ships an MCP server that exposes the pipeline as tools for any MCP-compatible client (Claude Desktop, Claude CLI, OpenAI Agents SDK, etc.).

Start the server:

```bash
biolit-mcp
```

Or test interactively with the MCP inspector:

```bash
mcp dev biolit/mcp_server.py
```

### Configure Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "biolit": {
      "command": "biolit-mcp"
    }
  }
}
```

Restart Claude Desktop. The tools will appear in the tool picker.

### Configure Claude CLI

Add a `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "biolit": {
      "command": "biolit-mcp"
    }
  }
}
```

### Available tools

**Batch pipeline** (equivalent to the `biolit` CLI):

| Tool | Description |
|---|---|
| `run_pipeline` | Fetch, optionally screen, and optionally extract a mixed list of PMIDs, DOIs, and/or GEO accessions; write results CSV (and optionally a `.md` summary when `markdown=True`). Accepts `ids` (comma-separated), `bib_path` (`.bib` file), or `ids_file` (plain-text identifier file). All parameters optional — pass only `config_path` to drive the entire run from a JSON file. |

**Low-level** (for custom workflows):

| Tool | Description |
|---|---|
| `fetch_pubmed_metadata` | Fetch PubMed metadata by PMID |
| `fetch_geo_record` | Fetch and parse a GEO record by accession |
| `fetch_fulltext` | Retrieve full text for a PMID (6-step chain) |
| `fetch_geo_fulltext` | Retrieve full text for a GEO accession via its linked PMIDs |
| `screen_paper` | LLM relevance screen given pre-fetched text |
| `extract_fields` | Structured field extraction given pre-fetched text |
| `resolve_doi` | Resolve a DOI to PMID + PMCID via the NCBI ID Converter |
| `lookup_s2_pdf` | Check whether Semantic Scholar has an open-access PDF for a DOI |
| `read_pmids_from_eml` | Parse PMIDs from a PubMed alert `.eml` file |
| `get_version` | Return the installed biolit package version |

### Use as a Python library

The pipeline functions are importable directly:

```python
from biolit.pipeline import run, screen_paper, fetch_record
from biolit.llm import get_llm_client

client = get_llm_client("anthropic")

# Batch pipeline — PMIDs, DOIs, and GEO accessions can be mixed freely
# criterion and fields_description are optional; omit either to skip that step
# markdown=True writes results.md alongside the CSV
# Returns (csv_path, record_count)
csv_path, count = run(client, ids=["41627908", "GSE53987", "10.1101/2025.03.17.25324098"],
    criterion="...", fields_description="methodology, summary", output_path="results.csv",
    markdown=True)

# Fetch + write metadata only (no LLM calls)
csv_path, count = run(client, ids=["41627908", "GSE53987"])

# Fetch a single record (auto-detects PMID / DOI / GEO)
paper = fetch_record("10.1101/2025.03.17.25324098")

# Screen pre-fetched text
result = screen_paper(client, paper, "Is this about schizophrenia genomics?", paper["abstract"])
# {"relevant": True, "reason": "..."}
```

## Known Limitations

- Papers without abstracts or accessible full text are skipped silently.
- GEO records attempt full-text retrieval via linked PMIDs. `text_source` will be `geo_linked_fulltext`, `geo_linked_abstract`, or `geo_record` depending on what was accessible.
- bioRxiv/medRxiv JATS XML is frequently blocked by Cloudflare regardless of headers. The pipeline falls back to the title and abstract from the bioRxiv API (`text_source: preprint_abstract`).
- The Semantic Scholar API allows roughly 100 unauthenticated requests per day. Set `SEMANTIC_SCHOLAR_API_KEY` in `.env` for higher limits.

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

Or interactively (prompted if not provided):

```bash
biolit alert.eml
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

### Full-text retrieval (PubMed inputs only)

Full-text retrieval runs automatically for every paper. The pipeline tries each source in order, falling back to the abstract if nothing is available:

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
- `artifacts/<id>/` — per-record folder with the text sent to the LLM, metadata, and any retrieved full-text files

With `--default`, the CSV columns are:

| Column | Description |
|---|---|
| `title` | Paper title |
| `url` | Link to PubMed, GEO, or DOI |
| `pmid` | PubMed ID (null for unindexed preprints) |
| `doi` | DOI (null for GEO records) |
| `geo_accession` | GEO accession (null for non-GEO records) |
| `text_source` | Where the text came from (`abstract`, `pmc_fulltext`, `europepmc_fulltext`, `preprint_fulltext`, `unpaywall_pdf`, `s2_pdf`, `geo_metadata`) |
| `citation_count` | Citation count from Semantic Scholar (null if not found) |
| `methodology` | General method (e.g. GWAS, scRNA-seq, proteomics) |
| `sample_type` | Tissue/sample type and origin |
| `causal_claims` | Statements about causes of schizophrenia inferred from the data |
| `genetics_claims` | Claims about specific genes, loci, or pathways |
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
| `run_pipeline` | Screen + extract a mixed list of PMIDs, DOIs, and/or GEO accessions; write results CSV |

**Single-record** (equivalent to `biolit screen`):

| Tool | Description |
|---|---|
| `screen_by_pmid` | Fetch + screen a PubMed paper in one call |
| `screen_by_doi` | Fetch + screen a paper by DOI in one call (handles preprints with no PMID) |
| `screen_by_geo` | Fetch + screen a GEO record in one call |

**Low-level** (for custom workflows):

| Tool | Description |
|---|---|
| `search_pubmed` | Fetch PubMed metadata by PMID |
| `fetch_geo_record` | Fetch and parse a GEO record by accession |
| `fetch_fulltext` | Retrieve full text for a PMID (6-step chain) |
| `screen_paper` | LLM relevance screen given pre-fetched text |
| `extract_fields` | Structured field extraction given pre-fetched text |
| `resolve_doi` | Resolve a DOI to PMID + PMCID via the NCBI ID Converter |
| `lookup_s2_pdf` | Check whether Semantic Scholar has an open-access PDF for a DOI |
| `read_pmids_from_eml` | Parse PMIDs from a PubMed alert `.eml` file |

### Use as a Python library

The pipeline functions are importable directly:

```python
from biolit.pipeline import screen_by_pmid, screen_by_doi, screen_by_geo, run
from biolit.llm import get_llm_client

client = get_llm_client("anthropic")

# Screen by PMID
result = screen_by_pmid(client, "41627908", "Is this about schizophrenia genomics?")
# {"relevant": True, "reason": "...", "text_source": "abstract"}

# Screen by DOI (works for preprints without a PMID)
result = screen_by_doi(client, "10.1101/2025.03.17.25324098", "Is this about schizophrenia genomics?")
# {"relevant": True, "reason": "...", "text_source": "preprint_fulltext", "doi": "..."}

# Batch pipeline — PMIDs, DOIs, and GEO accessions can be mixed freely
run(client, ids=["41627908", "GSE53987", "10.1101/2025.03.17.25324098"],
    criterion="...", fields_description="methodology, summary", output_path="results.csv")
```

## Known Limitations

- Preprint DOIs not yet indexed in PubMed (no PMID) are handled natively via the bioRxiv/medRxiv API — they are not skipped.
- Papers without abstracts or accessible full text are skipped silently.
- Full-text retrieval applies to PubMed and DOI inputs only; GEO records always use the record metadata directly.
- bioRxiv/medRxiv JATS XML is frequently blocked by Cloudflare regardless of headers. The pipeline falls back to the title and abstract from the bioRxiv API (`text_source: preprint_abstract`).
- DOIs passed via `--dois` or a DOI file are resolved to PMIDs before the batch pipeline runs. DOIs that can't be resolved (e.g. preprints not yet indexed in PubMed) are skipped. Use `biolit screen --doi` to screen an individual unresolvable DOI.
- The Semantic Scholar API allows roughly 100 unauthenticated requests per day. Set `SEMANTIC_SCHOLAR_API_KEY` in `.env` for higher limits.

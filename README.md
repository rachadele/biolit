# biolit

LLM-assisted biomedical literature screening and structured extraction. Accepts PubMed alert emails, PMID lists, DOI lists, or GEO accession lists. Retrieves full text from PMC, Europe PMC, bioRxiv/medRxiv, Unpaywall, and Semantic Scholar. Supports multiple LLM providers and exposes all functionality as an MCP server.

## Setup

**Requirements:** Python 3.8+

Install the package (creates the `biolit` command):

```bash
pip install -e .
```

Copy `.env.example` to `.env` and add your API key:

```bash
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY (or OPENAI_API_KEY)
```

## Usage

The tool accepts several input formats, auto-detected by file extension or content:

| Input | How to pass | Example |
|---|---|---|
| PubMed alert email | positional `.eml` file | `alert.eml` |
| PMID list (file) | positional plain-text file, one PMID per line | `pmids.txt` |
| DOI list (file) | positional plain-text file, one DOI per line | `dois.txt` |
| GEO accession list (file) | positional plain-text file, one accession per line | `geo_accessions.txt` |
| PMIDs (inline) | `--pmids` flag, comma-separated | `--pmids 41795042,41792186` |
| DOIs (inline) | `--dois` flag, comma-separated | `--dois 10.1038/s41588-021-00974-7` |
| GEO accessions (inline) | `--accessions` flag, comma-separated | `--accessions GSE53987,GSE12345` |

Use `--default` to run with schizophrenia genomics defaults (no prompts):

```bash
biolit docs/alert.eml --default
biolit docs/pmids.txt --default
biolit docs/geo_accessions.txt --default
biolit --pmids 41795042,41792186 --default
biolit --accessions GSE53987 --default
```

Or specify criterion and fields as flags:

```bash
biolit docs/pmids.txt \
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
biolit screen --pmid 41627908 --fulltext --default
```

Output is a single line to stdout:

```
RELEVANT [abstract] — Paper uses GWAS to investigate schizophrenia risk loci.
```

### GEO accession input

Pass a file of GEO series accessions (GSE, GDS, GSM, or GPL prefixes) to screen GEO records directly. The tool fetches each record's MINiML XML, extracts the summary, overall design, experiment type, and organism, then runs the same LLM screening and extraction pipeline.

```bash
biolit geo_accessions.txt \
  --criterion "Does this study perturb a transcription factor?" \
  --fields "organism, experiment_type, tf_perturbed, perturbation_method, summary"
```

GEO results include `geo_accession` and `pmids` (linked PubMed IDs) columns in place of `pmid`.

### Full-text retrieval (PubMed inputs only)

Use `--fulltext` to screen and extract from full text instead of just the abstract. The pipeline tries each source in order:

1. PMC JATS XML (open access)
2. Europe PMC JATS XML (broader open-access coverage)
3. Preprint XML (bioRxiv / medRxiv)
4. Unpaywall PDF (requires `--unpaywall-email`)
5. Semantic Scholar open-access PDF
6. Abstract fallback

```bash
biolit alert.eml --default --fulltext --unpaywall-email you@example.com
```

Limit which sections are sent to the LLM:

```bash
biolit alert.eml --default --fulltext --sections methods,results
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

With `--default` on PubMed inputs, the CSV columns are:

| Column | Description |
|---|---|
| `title` | Paper title |
| `url` | PubMed link |
| `pmid` | PubMed ID |
| `doi` | DOI |
| `text_source` | Where the text came from (`abstract`, `pmc_fulltext`, `europepmc_fulltext`, `preprint_fulltext`, `unpaywall_pdf`, `s2_pdf`) |
| `methodology` | General method (e.g. GWAS, scRNA-seq, proteomics) |
| `sample_type` | Tissue/sample type and origin |
| `causal_claims` | Statements about causes of schizophrenia inferred from the data |
| `genetics_claims` | Claims about specific genes, loci, or pathways |
| `summary` | 2-3 sentence plain-language summary for triage |

For GEO inputs, `pmid` is replaced by `geo_accession` and `pmids`.

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

**Batch pipelines** (equivalent to the `biolit` CLI):

| Tool | Description |
|---|---|
| `run_pipeline` | Screen + extract a list of PMIDs, write results CSV |
| `run_geo_pipeline` | Screen + extract a list of GEO accessions, write results CSV |

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
from biolit.pipeline import screen_by_pmid, screen_by_doi, screen_by_geo, run, run_geo
from biolit.llm import get_llm_client

client = get_llm_client("anthropic")

# Screen by PMID
result = screen_by_pmid(client, "41627908", "Is this about schizophrenia genomics?")
# {"relevant": True, "reason": "...", "text_source": "abstract"}

# Screen by DOI (works for preprints without a PMID)
result = screen_by_doi(client, "10.64898/2026.02.16.706214", "Is this about schizophrenia genomics?")
# {"relevant": True, "reason": "...", "text_source": "preprint_abstract", "doi": "..."}

# Batch pipeline
run(client, pmids=["41627908", "33741721"], criterion="...", fields_description="methodology, summary", output_path="results.csv")
```

## Known Limitations

- Papers without abstracts or accessible full text are skipped silently.
- Full-text retrieval (`--fulltext`) applies to PubMed and DOI inputs only; GEO records always use the record metadata directly.
- bioRxiv/medRxiv JATS XML is frequently blocked by Cloudflare regardless of headers. The pipeline falls back to the title and abstract from the bioRxiv API (`text_source: preprint_abstract`).
- DOIs passed via `--dois` or a DOI file are resolved to PMIDs before the batch pipeline runs. DOIs that can't be resolved (e.g. preprints not yet indexed in PubMed) are skipped. Use `biolit screen --doi` to screen an individual unresolvable DOI.
- The Semantic Scholar API allows roughly 100 unauthenticated requests per day. Set `SEMANTIC_SCHOLAR_API_KEY` in `.env` for higher limits.

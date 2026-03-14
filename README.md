# PubMed Literature Screener

Screens biomedical literature for relevant records and extracts structured information into a CSV. Accepts PubMed alert emails, plain PMID lists, or GEO accession lists. Supports multiple LLM providers and optional full-text retrieval.

## Setup

**Requirements:** Python 3.8+

Install the package (creates the `pubmed-screener` command):

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
| GEO accession list (file) | positional plain-text file, one accession per line | `geo_accessions.txt` |
| PMIDs (inline) | `--pmids` flag, comma-separated | `--pmids 41795042,41792186` |
| GEO accessions (inline) | `--accessions` flag, comma-separated | `--accessions GSE53987,GSE12345` |

Use `--default` to run with schizophrenia genomics defaults (no prompts):

```bash
pubmed-screener alert.eml --default
pubmed-screener pmids.txt --default
pubmed-screener geo_accessions.txt --default
pubmed-screener --pmids 41795042,41792186 --default
pubmed-screener --accessions GSE53987 --default
```

Or specify criterion and fields as flags:

```bash
pubmed-screener pmids.txt \
  --criterion "Is this about treatment-resistant schizophrenia?" \
  --fields "methodology, sample_size, treatment, outcomes"
```

Or interactively (prompted if not provided):

```bash
pubmed-screener alert.eml
```

### GEO accession input

Pass a file of GEO series accessions (GSE, GDS, GSM, or GPL prefixes) to screen GEO records directly. The tool fetches each record's MINiML XML, extracts the summary, overall design, experiment type, and organism, then runs the same LLM screening and extraction pipeline.

```bash
pubmed-screener geo_accessions.txt \
  --criterion "Does this study perturb a transcription factor?" \
  --fields "organism, experiment_type, tf_perturbed, perturbation_method, summary"
```

GEO results include `geo_accession` and `pmids` (linked PubMed IDs) columns in place of `pmid`.

### Full-text retrieval (PubMed inputs only)

Use `--fulltext` to screen and extract from full text instead of just the abstract. The pipeline tries each source in order:

1. PMC JATS XML (open access)
2. Preprint XML (bioRxiv / medRxiv)
3. Unpaywall PDF (requires `--unpaywall-email`)
4. Abstract fallback

```bash
pubmed-screener alert.eml --default --fulltext --unpaywall-email you@example.com
```

Limit which sections are sent to the LLM:

```bash
pubmed-screener alert.eml --default --fulltext --sections methods,results
```

### LLM providers

The tool supports Anthropic (default), OpenAI, and local Ollama models:

```bash
# OpenAI
pubmed-screener pmids.txt --default --provider openai --model gpt-4o

# Ollama (local)
pubmed-screener pmids.txt --default --provider ollama --model llama3
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
| `text_source` | Where the text came from (`abstract`, `pmc_fulltext`, `preprint_fulltext`, `unpaywall_pdf`) |
| `methodology` | General method (e.g. GWAS, scRNA-seq, proteomics) |
| `sample_type` | Tissue/sample type and origin |
| `causal_claims` | Statements about causes of schizophrenia inferred from the data |
| `genetics_claims` | Claims about specific genes, loci, or pathways |
| `summary` | 2-3 sentence plain-language summary for triage |

For GEO inputs, `pmid` is replaced by `geo_accession` and `pmids`.

The CSV can be imported directly into Google Sheets (File → Import).

## Known Limitations

- Papers without abstracts or accessible full text are skipped silently.
- Full-text retrieval (`--fulltext`) applies to PubMed inputs only; GEO records use the record metadata directly.

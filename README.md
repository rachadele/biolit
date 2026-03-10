# PubMed Literature Screener

Screens PubMed alert emails for relevant papers and extracts structured information into a CSV using the Claude API.

For each paper in a PubMed alert, the tool:
1. Fetches the abstract and MeSH terms from NCBI
2. Uses Claude to decide if the paper is relevant (schizophrenia + genomics methods)
3. Extracts structured fields from relevant papers: methodology, sample type, causal claims, genetics claims, and a plain-language summary

## Setup

**Requirements:** Python 3.8+

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your [Anthropic API key](https://console.anthropic.com/):

```bash
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY
```

## Usage

Save your PubMed alert email as a `.eml` file (in Gmail: three-dot menu → Download message), then run:

```bash
python screen.py alert.eml
```

Results are written to `results.csv` by default. To specify a different output file:

```bash
python screen.py alert.eml --output my_results.csv
```

## Output

The CSV contains one row per relevant paper with these columns:

| Column | Description |
|---|---|
| `title` | Paper title |
| `url` | PubMed link |
| `pmid` | PubMed ID |
| `methodology` | General method (e.g. GWAS, scRNA-seq, proteomics) |
| `sample_type` | Tissue/sample type and origin |
| `causal_claims` | Statements about causes of schizophrenia inferred from the data |
| `genetics_claims` | Claims about specific genes, loci, or pathways |
| `summary` | 2-3 sentence plain-language summary for triage |

The CSV can be imported directly into Google Sheets (File → Import).

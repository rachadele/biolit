# Paper Ideas

## Potential paper: Biomedical literature screening as an MCP server

**Authorship:** Rachel (first), Paul (senior/last)

**Target journals:** Bioinformatics, JOSS (Journal of Open Source Software), GigaScience

---

### Concept

A software/methods paper presenting `biolit` as a **Model Context Protocol (MCP)
server** for LLM-assisted biomedical literature screening and structured field extraction.

MCP (released late 2024) is Anthropic's open protocol for connecting LLMs to external tools
and data sources. Although created by Anthropic, MCP is open and provider-agnostic — OpenAI,
Google Gemini, and major agent frameworks (LangChain, LlamaIndex, AutoGen) all adopted it by
early 2025. Any MCP-compatible client, regardless of which LLM it runs, can call a `biolit`
MCP server. By implementing `biolit` as an MCP server, it becomes composable infrastructure
rather than a standalone pipeline — any MCP-compatible client (Claude Desktop, OpenAI Agents
SDK, other agents) can call its tools dynamically and orchestrate them as part of larger
workflows.

`biolit` is usable three ways from a single `pip install`:

| Mode | How to use | When to use |
|---|---|---|
| Python library | `from biolit.pipeline import run` | Embed in your own scripts or notebooks |
| CLI pipeline | `biolit alert.eml --default` | Run a batch screening job from the terminal |
| MCP server | `biolit-mcp` | Connect to Claude Desktop, OpenAI Agents, or any MCP client |

This is the technical novelty: not the API calls themselves, but the architecture that makes
domain-specific biomedical curation tools available as first-class LLM-callable services —
from any provider, via a single installable package.

---

### Why MCP framing is stronger than "pipeline wrapper"

- MCP is new (late 2024) with very few domain-specific servers published in academic contexts
- Demonstrates a reproducible pattern for how scientific tools can be exposed to LLM agents
- Shifts from "we automated a workflow" to "we built composable infrastructure for
  LLM-assisted systematic review"
- Timely: agentic scientific workflows are a hot topic; this is a concrete, useful example

---

### Example use case: reproducibility of findings across schizophrenia genomics studies

The MCP server is used by an LLM agent to screen a corpus of schizophrenia genomics papers
and extract structured fields (methodology, sample type, causal claims, genomic loci).
The extracted data is then used to ask: do studies using different methods (GWAS vs. WGS
vs. scRNA-seq) reach consistent conclusions about the same loci or mechanisms?

This frames the tool as enabling a kind of meta-analysis that would otherwise require months
of manual curation — and shows a real scientific result, not just a demonstration.

A second use case (GEO studies, e.g. "does this dataset perturb TF X?") could demonstrate
generalizability beyond PubMed — Paul has this in mind for Kevin's work.

---

### Current capabilities (CLI pipeline)

The `biolit` CLI pipeline is fully implemented and tested:

- **PubMed inputs:** `.eml` alert emails, plain-text PMID files, or `--pmids` flag
- **GEO inputs:** plain-text GEO accession files or `--accessions` flag (GSE/GDS/GSM/GPL)
- **Full-text retrieval:** PMC JATS XML → preprint XML → Unpaywall PDF → abstract fallback
- **LLM providers:** Anthropic (default), OpenAI, Ollama (local)
- **Configurable:** any screening criterion and any extraction fields
- **Structured output:** timestamped run directories with `results.csv` and per-record artifacts

The GEO pipeline fetches MINiML XML and runs the same LLM screening/extraction as PubMed — demonstrating that the same interface works across heterogeneous biomedical data sources.

---

### MCP server design

The CLI pipeline stays as-is. A new `biolit/mcp_server.py` wraps the same
underlying functions as MCP tools (~100-200 lines, not a rewrite).

**Tools to expose:**

| Tool | Description |
|---|---|
| `search_pubmed` | Fetch metadata for a PMID (title, abstract, MeSH, DOI) |
| `fetch_geo_record` | Fetch and parse a GEO accession record (summary, design, experiment type, organisms) |
| `fetch_fulltext` | Retrieve full text from PMC, preprint servers, or Unpaywall |
| `screen_paper` | Ask the LLM whether a paper meets a relevance criterion |
| `extract_fields` | Extract structured fields from paper text |
| `read_pmids_from_eml` | Parse PMIDs from a PubMed alert .eml file |

**Resources to expose:**

| Resource | URI pattern | Description |
|---|---|---|
| Paper metadata | `pubmed://pmid/{pmid}` | Structured metadata for a PMID |
| Full text | `pubmed://fulltext/{pmid}` | Full text if available, else abstract |
| GEO record | `geo://accession/{accession}` | Parsed GEO record metadata |

An LLM agent can then orchestrate these tools dynamically — e.g. fetch a list of PMIDs,
screen each one, fetch full text only for relevant papers, extract fields, and return a
structured table — without any fixed pipeline code.

---

### What would make a strong paper

1. **Gold standard benchmark** — manually curated extraction for ~50-100 papers to measure
   LLM precision/recall on each field (methodology, sample_type, causal_claims, etc.)
2. **Multi-LLM comparison** — the tool supports Anthropic, OpenAI, and Ollama; evaluate
   extraction quality vs. cost across providers (Haiku vs. GPT-4o vs. local model)
3. **Scale** — run on a few hundred papers to show feasibility
4. **A concrete reproducibility finding** — the analysis should produce a real result,
   not just a demonstration
5. **GEO integration as Section 2** — show the same pipeline applied to GEO datasets
   (e.g., "does this dataset perturb TF X?" — relevant to Kevin's work)
6. **MCP interoperability demo** — show the server being called from Claude Desktop or
   another MCP client to demonstrate composability

---

### Novelty framing

- **Protocol-level**: one of the first published MCP servers for biomedical literature curation; provider-agnostic (works with Claude, GPT-4, local models)
- **Application**: reproducibility analysis at scale in schizophrenia genomics
- **Evaluation**: benchmarking LLMs on structured biomedical extraction across providers
- **Generalizability**: PubMed, GEO, or any record list as input via the same tool interface

---

### Next steps before writing

- [x] Implement CLI pipeline for PubMed inputs (`.eml`, PMID list, `--pmids`)
- [x] Implement GEO accession input (`fetch_geo_record`, `run_geo`, `--accessions`)
- [x] Support multiple LLM providers (Anthropic, OpenAI, Ollama)
- [x] Full-text retrieval (PMC, preprints, Unpaywall)
- [x] Rename package to `biolit`; publish to GitHub
- [ ] Implement `biolit/mcp_server.py` with the tools listed above
- [ ] Define the reproducibility question precisely (which loci / which claims to track)
- [ ] Build a gold standard: manually extract fields from ~50 papers
- [ ] Run the tool on a larger corpus and benchmark against gold standard
- [ ] Demo MCP server from Claude Desktop

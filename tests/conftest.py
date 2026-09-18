"""Shared pytest fixtures."""
import os
import pathlib

import pytest
from dotenv import load_dotenv

load_dotenv(override=True)

# fetch_paper() caches full-text results to disk keyed by pmid/doi/accession
# alone (not by mocked source), so distinct tests reusing the same id (e.g.
# pmid "12345") would otherwise read back another test's cached full text
# instead of exercising their own mocks. Disable the on-disk cache for the
# whole suite — tests always mock the network, so there's nothing to cache.
os.environ["BIOLIT_PAPER_CACHE_DISABLE"] = "1"

# Clear any fetchers auto-registered from the developer's .env (BIOLIT_BIBTEX,
# BIOLIT_LOCAL_PDF_DIR, ZOTERO_API_KEY, ...) so the test suite isn't
# contaminated by the local environment. Tests that exercise the registry
# register their own fetchers explicitly.
from biolit.fetchers import _hooks as _fetcher_hooks
_fetcher_hooks._REGISTRY.clear()

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"

# PMIDs present in test1.eml (18 articles from the 2026-03-08 "schizophrenia genomics" alert)
TEST1_PMIDS = [
    "41795042", "41792186", "41785323", "41784660", "41780463",
    "41779404", "41775981", "41773067", "41771789", "41767305",
    "41767152", "41764180", "41764167", "41764057", "41754588",
    "41690235", "41627908", "41611717",
]


@pytest.fixture
def eml_path() -> pathlib.Path:
    """Path to the stub PubMed alert .eml file (synthetic, 2 papers)."""
    return FIXTURES_DIR / "pubmed_alert.eml"


@pytest.fixture
def test1_eml_path() -> pathlib.Path:
    """Path to the real PubMed alert .eml file (18 papers, 2026-03-08)."""
    return FIXTURES_DIR / "test1.eml"


@pytest.fixture
def sample_pubmed_metadata():
    """A minimal paper metadata dict as returned by fetch_pubmed_metadata."""
    return {
        "pmid": "41795042",
        "doi": "10.1038/s41588-026-01234-5",
        "title": "Genome-wide association study of schizophrenia in a European cohort",
        "abstract": (
            "We performed a genome-wide association study (GWAS) of schizophrenia "
            "in 50,000 cases and 80,000 controls of European ancestry. We identified "
            "47 genome-wide significant loci and implicated synaptic genes."
        ),
        "mesh_terms": ["Schizophrenia", "Genome-Wide Association Study", "Genomics"],
        "url": "https://pubmed.ncbi.nlm.nih.gov/41795042/",
        "fulltext_xml": None,
        "fulltext_pdf": None,
    }


@pytest.fixture
def sample_jats_xml() -> bytes:
    """Minimal JATS XML resembling a PMC full-text record."""
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<article>
  <front>
    <article-meta>
      <abstract>
        <p>This is the structured abstract from PMC JATS XML.</p>
      </abstract>
    </article-meta>
  </front>
  <body>
    <sec>
      <title>Introduction</title>
      <p>Schizophrenia is a complex polygenic disorder affecting ~1% of the population.</p>
    </sec>
    <sec>
      <title>Methods</title>
      <p>We genotyped 130,000 individuals on the Illumina GSA array and imputed to the
      TOPMed reference panel. Association testing used SAIGE with age, sex, and 10 PCs
      as covariates.</p>
    </sec>
    <sec>
      <title>Results</title>
      <p>We identified 47 genome-wide significant loci (p &lt; 5e-8). The top hit was
      rs1234567 at the MHC locus (OR=1.12, p=2.3e-45).</p>
    </sec>
    <sec>
      <title>Discussion</title>
      <p>Our findings support a synaptic model of schizophrenia risk.</p>
    </sec>
  </body>
</article>
"""


@pytest.fixture
def floats_group_jats_xml() -> bytes:
    """JATS XML in PMC's HOISTED-FLOAT layout.

    PMC lifts every <table-wrap> and <fig> out of the body into a
    <floats-group> sibling of <body>, leaving only an <xref> in the prose.
    Modelled on PMID 38761795 (Mol Cell 2024), whose only <table-wrap> sits at
    /pmc-articleset/article/floats-group/table-wrap, is captioned "Key
    resources table" and carries 13 RRIDs — none of which reached the parsed
    text before the floats collector existed.

    The <fig> is here so tests can pin that figure captions are NOT swept in
    with the tables.
    """
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<pmc-articleset>
<article>
  <front>
    <article-meta>
      <abstract>
        <p>We profiled human monocytes.</p>
      </abstract>
    </article-meta>
  </front>
  <body>
    <sec>
      <title>Results</title>
      <p>Reagents are listed in the
      <xref ref-type="table" rid="T1">key resources table</xref>.</p>
    </sec>
    <sec>
      <title>STAR Methods</title>
      <sec>
        <title>Cell culture</title>
        <p>Cells were maintained in RPMI-1640.</p>
      </sec>
    </sec>
  </body>
  <floats-group>
    <fig id="F1">
      <label>Figure 1</label>
      <caption><p>UMAP embedding of monocytes.</p></caption>
    </fig>
    <table-wrap id="T1">
      <caption><p>Key resources table</p></caption>
      <table>
        <thead>
          <tr><th>REAGENT or RESOURCE</th><th>SOURCE</th><th>IDENTIFIER</th></tr>
        </thead>
        <tbody>
          <tr><td>Antibodies</td><td></td><td></td></tr>
          <tr><td>anti-human CD14 Antibody</td><td>BioLegend</td>
              <td>Cat# 325611; RRID: AB_830684</td></tr>
          <tr><td>Experimental models: Cell lines</td><td></td><td></td></tr>
          <tr><td>THP-1</td><td>ATCC</td>
              <td>Cat# TIB-202; RRID: CVCL_0006</td></tr>
        </tbody>
      </table>
    </table-wrap>
  </floats-group>
</article>
</pmc-articleset>
"""

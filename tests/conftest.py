"""Shared pytest fixtures."""
import pathlib
import pytest

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



"""Unit tests for the JATS XML and section-selection parsers."""
import io

import pytest

from biolit.parsers.docx import extract_docx_text
from biolit.parsers.jats import parse_jats_sections
from biolit.parsers.utils import select_sections


class TestExtractDocxText:
    def test_extracts_paragraphs_and_tables(self):
        docx = pytest.importorskip("docx")  # python-docx
        doc = docx.Document()
        doc.add_paragraph("Supplementary Methods")
        doc.add_paragraph("Mice were on a C57BL/6 background.")
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "Antibody"
        table.rows[0].cells[1].text = "anti-GFP"
        buf = io.BytesIO()
        doc.save(buf)
        text = extract_docx_text(buf.getvalue())
        assert "Supplementary Methods" in text
        assert "C57BL/6" in text
        assert "Antibody\tanti-GFP" in text

    def test_returns_empty_on_garbage(self):
        assert extract_docx_text(b"not a docx") == ""


class TestParseJatsSections:
    def test_extracts_known_sections(self, sample_jats_xml):
        secs = parse_jats_sections(sample_jats_xml)
        assert "introduction" in secs
        assert "methods" in secs
        assert "results" in secs
        assert "discussion" in secs

    def test_extracts_abstract_element(self, sample_jats_xml):
        secs = parse_jats_sections(sample_jats_xml)
        assert "abstract" in secs
        assert "structured abstract" in secs["abstract"].lower()

    def test_methods_content_present(self, sample_jats_xml):
        secs = parse_jats_sections(sample_jats_xml)
        assert "SAIGE" in secs["methods"] or "genotyped" in secs["methods"]

    def test_results_content_present(self, sample_jats_xml):
        secs = parse_jats_sections(sample_jats_xml)
        assert "47" in secs["results"]

    def test_returns_empty_dict_on_garbage_input(self):
        result = parse_jats_sections(b"this is not xml <<<")
        assert isinstance(result, dict)

    def test_returns_dict_on_empty_bytes(self):
        result = parse_jats_sections(b"")
        assert isinstance(result, dict)

    def test_block_boundaries_are_separated_inline_tags_stay_glued(self):
        """Per 2026-05-09 audit: heading/paragraph block boundaries
        used to glue (`MethodsContact`, `DetailsMice`, `MiceWe`)
        because ``''.join(itertext())`` had no separator. Inline tags
        like ``<sup>`` mid-token must NOT introduce a separator —
        compound terms (``Foxp3creYFP``, ``HDAC6KO``, ``mtND6mut``)
        depend on staying glued."""
        xml = (
            b"<article><body>"
            b"<sec><title>Methods</title>"
            b"<p>Contact for Reagent Sharing</p>"
            b"<sec><title>Mice</title>"
            b"<p>We used Foxp3<sup>creYFP</sup>Mice and "
            b"HDAC6<sup>KO</sup> animals.</p>"
            b"</sec></sec></body></article>"
        )
        secs = parse_jats_sections(xml)
        methods = secs.get("methods", "")
        # Block boundaries split.
        assert "MethodsContact" not in methods
        assert "SharingMice" not in methods
        assert "MiceWe" not in methods
        # Inline-tag-bridged compound terms stay intact.
        assert "Foxp3creYFPMice" in methods
        assert "HDAC6KO" in methods

    def test_extracts_footnote_data_availability_statement(self):
        """PMID 29631039: GEO accession statements are often published as
        a <fn> in <back>/<fn-group>, not a body <sec>, and were previously
        silently dropped."""
        xml = (
            b"<article><back><fn-group><fn><p>Data are available under "
            b'GEO accession numbers <ext-link ext-link-type="pmc:entrez-geo" '
            b'xlink:href="GSE70823">GSE70823</ext-link> and '
            b'<ext-link ext-link-type="pmc:entrez-geo" '
            b'xlink:href="GSE102352">GSE102352</ext-link>.</p></fn>'
            b"</fn-group></back></article>"
        )
        secs = parse_jats_sections(xml)
        assert "GSE70823" in secs["footnotes"]
        assert "GSE102352" in secs["footnotes"]

    def test_extracts_notes_data_availability_statement(self):
        """PMID 37940970: GEO accession statements are also published as a
        <notes> element (usually under <back>), a separate JATS tag from
        <fn> used for the same kind of content and previously dropped."""
        xml = (
            b'<article><back><notes notes-type="data-availability">'
            b"<title>Availability of data and materials</title>"
            b"<p>The dataset supporting the conclusions of this article is "
            b"available in GEO repository with accession number of "
            b'<ext-link ext-link-type="pmc:entrez-geo" '
            b'xlink:href="GSE210470">GSE210470</ext-link>.</p>'
            b"</notes></back></article>"
        )
        secs = parse_jats_sections(xml)
        assert "GSE210470" in secs["notes"]

    def test_nested_subsection_not_emitted_as_separate_key(self):
        """A nested subsection's text is already inside its parent's
        section, so it must NOT also be emitted as its own top-level key
        — that duplicated Methods content and wasted the excerpt budget
        under truncation (2026-07-31). ``methods`` keeps the child text;
        there is no separate ``mice`` key."""
        xml = (
            b"<article><body>"
            b"<sec><title>Methods</title>"
            b"<sec><title>Mice</title>"
            b"<p>Mice were used between the ages of 6 to 8 weeks.</p>"
            b"</sec>"
            b"<sec><title>Flow cytometry</title>"
            b"<p>Cells were stained with anti-CD3.</p>"
            b"</sec></sec>"
            b"<sec><title>Results</title><p>We found 47 genes.</p></sec>"
            b"</body></article>"
        )
        secs = parse_jats_sections(xml)
        # Parent keeps all nested content.
        assert "6 to 8 weeks" in secs["methods"]
        assert "anti-CD3" in secs["methods"]
        # Nested children are NOT separate keys (no duplication).
        assert "mice" not in secs
        assert "flow cytometry" not in secs
        # Sibling top-level section is unaffected.
        assert "47" in secs["results"]

    def test_flat_methods_subsections_all_kept(self):
        """When Methods subsections are top-level <sec>s (no parent
        wrapper), every one is kept — the top-level-only rule only drops
        genuinely-nested duplicates."""
        xml = (
            b"<article><body>"
            b'<sec sec-type="methods"><title>Mice</title>'
            b"<p>C57BL/6 at 8 weeks.</p></sec>"
            b'<sec sec-type="methods"><title>Sequencing</title>'
            b"<p>Illumina HiSeq.</p></sec>"
            b"</body></article>"
        )
        secs = parse_jats_sections(xml)
        assert "8 weeks" in secs.get("mice", "")
        assert "Illumina" in secs.get("sequencing", "")


class TestSelectSections:
    def test_returns_all_sections_when_wanted_is_none(self):
        secs = {"methods": "We did X.", "results": "We found Y."}
        out = select_sections(secs, wanted=None)
        assert "METHODS" in out
        assert "RESULTS" in out

    def test_filters_to_wanted_sections(self):
        secs = {"abstract": "A.", "methods": "M.", "results": "R.", "discussion": "D."}
        out = select_sections(secs, wanted=["methods", "results"])
        assert "METHODS" in out
        assert "RESULTS" in out
        assert "ABSTRACT" not in out
        assert "DISCUSSION" not in out

    def test_falls_back_to_all_when_wanted_not_matched(self):
        secs = {"body": "Full text here."}
        out = select_sections(secs, wanted=["methods"])
        # No methods section exists; should include everything
        assert "Full text here." in out

    def test_truncates_to_max_tokens(self):
        secs = {"body": "x" * 5000}
        out = select_sections(secs, max_tokens=25)  # 25 tokens * 4 = 100 chars
        assert len(out) <= 200  # header + truncation marker add a little overhead
        assert "truncated" in out

    def test_returns_empty_string_for_empty_sections(self):
        assert select_sections({}) == ""

    def test_section_headers_are_uppercased(self):
        secs = {"introduction": "Some text."}
        out = select_sections(secs)
        assert "=== INTRODUCTION ===" in out

    # --- an overflow must SKIP, not stop -------------------------------
    # Promotion protects the back-matter sections we can name by keyword.
    # These cover the ones we cannot: a later section that fits was being
    # dropped anyway, because the loop stopped at the first overflow.

    def test_a_later_section_that_fits_is_not_lost_to_an_earlier_overflow(self):
        secs = {
            "results": "r" * 60000,          # overflows the budget alone
            "odd_back_matter": "Accession GSE318045 lives here.",
        }
        out = select_sections(secs, max_tokens=12_500)
        assert "GSE318045" in out, "a short later section still fits"

    def test_overflow_does_not_swallow_the_budget_a_later_section_needs(self):
        """Truncation is deferred to a second pass for this reason: filling
        the tail greedily leaves nothing for a section that fits whole."""
        secs = {
            "big": "x" * 60000,
            "small_a": "alpha",
            "small_b": "beta",
        }
        out = select_sections(secs, max_tokens=12_500)
        assert "alpha" in out and "beta" in out

    def test_budget_is_still_respected(self):
        secs = {"a": "x" * 60000, "b": "y" * 60000, "c": "short"}
        out = select_sections(secs, max_tokens=12_500)
        assert len(out) <= 12_500 * 4 + 500      # + header / marker overhead

    def test_max_tokens_none_disables_the_budget(self):
        """A string search is not a prompt; it has no context window."""
        secs = {"results": "r" * 60000,
                "data availability": "Deposited under GSE318045."}
        out = select_sections(secs, max_tokens=None)
        assert len(out) > 59_000
        assert "truncated" not in out
        assert "GSE318045" in out

    def test_promotion_still_wins_under_a_budget(self):
        """Guards the behaviour already on main, which these changes must
        not regress: back-matter is promoted ahead of narrative."""
        secs = {"results": "r" * 60000,
                "data availability": "Deposited under GSE318045."}
        assert "GSE318045" in select_sections(secs, max_tokens=12_500)

    def test_data_availability_survives_truncation(self):
        secs = {
            "results": "x" * 5000,
            "data availability": "GSE210470",
        }
        out = select_sections(secs, max_tokens=25)  # 100 char budget
        assert "GSE210470" in out

    def test_normal_truncation_order_preserved_without_priority_sections(self):
        secs = {"methods": "M" * 5000, "results": "R" * 5000}
        out = select_sections(secs, max_tokens=25)
        assert out.startswith("=== METHODS ===")
        assert "RESULTS" not in out
        assert "truncated" in out

    def test_end_of_document_methods_survives_truncation(self):
        # Reproduces the Disease Models & Mechanisms layout (PMID 23580197 /
        # GSE34305): Introduction -> Results -> Discussion -> Materials and
        # Methods, i.e. Methods last. Without promotion, the preceding
        # narrative sections exhaust the char budget before Methods is ever
        # reached.
        secs = {
            "introduction": "I" * 5000,
            "results": "R" * 5000,
            "discussion": "D" * 5000,
            "materials and methods": "GSE34305 generated via RNA-seq",
        }
        out = select_sections(secs, max_tokens=25)  # 100 char budget
        assert "GSE34305 generated via RNA-seq" in out

    def test_structured_abstract_does_not_shadow_body_sections(self):
        """A structured abstract's <sec> subsections must not claim the
        ``methods`` / ``results`` keys ahead of the body's real sections.

        Abstract subsections are <sec> inside <abstract>, so "has no <sec>
        ancestor" admits them; being first in document order they won the key
        and the body's Methods was dropped by the ``key not in sections``
        guard. Measured on PMC4235044 (Mol Vis, abstract =
        Purpose/Methods/Results/Conclusions): ``methods`` returned 125
        characters of abstract instead of the paper's 7,196-character Methods,
        so the animal age never reached a consumer asking for Methods.
        """
        xml = (
            b"<article>"
            b"<front><article-meta><abstract>"
            b"<sec><title>Purpose</title><p>We asked a question.</p></sec>"
            b"<sec><title>Methods</title><p>Using RNA-Seq.</p></sec>"
            b"<sec><title>Results</title><p>We found things.</p></sec>"
            b"</abstract></article-meta></front>"
            b"<body>"
            b"<sec><title>Introduction</title><p>Background here.</p></sec>"
            b"<sec><title>Methods</title>"
            b"<sec><title>Animals</title>"
            b"<p>Retinas were dissected from mice 48 to 120 days old.</p></sec>"
            b"</sec>"
            b"<sec><title>Results</title><p>Rod transcripts were lost.</p></sec>"
            b"</body></article>"
        )
        secs = parse_jats_sections(xml)
        # The BODY's Methods wins, with its nested subsection text intact.
        assert "48 to 120 days old" in secs["methods"]
        assert "Using RNA-Seq" not in secs["methods"]
        assert "Rod transcripts were lost" in secs["results"]
        # Abstract-only headings never become body sections…
        assert "purpose" not in secs
        # …and the abstract itself is still captured whole.
        assert "We asked a question" in secs["abstract"]
        assert "Using RNA-Seq" in secs["abstract"]

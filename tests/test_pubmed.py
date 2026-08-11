"""Tests for the PubMed metadata fetcher."""
from unittest.mock import MagicMock, patch

from biolit.fetchers.pubmed import fetch_pubmed_metadata


def _xml_with_abstract(abstract_text_inner: str) -> bytes:
    return f"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>38064339</PMID>
      <Article>
        <ArticleTitle>Test title.</ArticleTitle>
        <Abstract>
          <AbstractText>{abstract_text_inner}</AbstractText>
        </Abstract>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>""".encode()


class TestFetchPubmedMetadataAbstract:
    @patch("biolit.fetchers.pubmed.time.sleep")
    @patch("biolit.fetchers.pubmed.requests.get")
    def test_abstract_includes_text_after_nested_inline_tag(self, mock_get, mock_sleep):
        # PubMed wraps chemical/gene names in inline tags like <sub>. Element.text
        # alone stops at the first child, dropping everything after it.
        mock_get.return_value = MagicMock(
            content=_xml_with_abstract(
                "Vitamin B<sub>12</sub> deficiency causes neurological manifestations."
            ),
            raise_for_status=MagicMock(),
        )
        result = fetch_pubmed_metadata("38064339")
        assert result["abstract"] == (
            "Vitamin B12 deficiency causes neurological manifestations."
        )

    @patch("biolit.fetchers.pubmed.time.sleep")
    @patch("biolit.fetchers.pubmed.requests.get")
    def test_plain_abstract_unchanged(self, mock_get, mock_sleep):
        # No-op check: a plain AbstractText with no nested tags still parses
        # to the exact same full text as before the fix.
        mock_get.return_value = MagicMock(
            content=_xml_with_abstract(
                "A plain abstract with no nested markup at all."
            ),
            raise_for_status=MagicMock(),
        )
        result = fetch_pubmed_metadata("38064339")
        assert result["abstract"] == "A plain abstract with no nested markup at all."

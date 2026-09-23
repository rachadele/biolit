"""Tests for biolit.parsers.pdf."""
from unittest.mock import patch

from biolit.parsers import pdf


def test_extract_text_disables_textbox_grouping():
    # pdfminer's boxes_flow grouping hung indefinitely on PMID 28485405.
    captured = {}

    def fake_extract(fp, out, laparams=None):
        captured["laparams"] = laparams
        out.write("text")

    with patch("pdfminer.high_level.extract_text_to_fp", side_effect=fake_extract):
        assert pdf._extract_text(b"%PDF-1.4") == "text"
    assert captured["laparams"].boxes_flow is None

"""Tests for the publisher landing-page HTML full-text fetcher."""
from unittest.mock import MagicMock, patch

from biolit.fetchers.landing_page_html import (
    _extract_article_text,
    classify_landing_page,
    fetch_landing_page_html,
)


def _mock_response(content: bytes, url: str = "https://publisher.example.org/article",
                   status_code: int = 200, encoding: str = "utf-8") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.text = content.decode("utf-8", errors="replace")
    resp.encoding = encoding
    resp.url = url
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


# A substantial article body (well over the JS-shell threshold).
_LONG_BODY = ("We genotyped 130,000 individuals and ran association testing with "
              "SAIGE adjusting for age, sex, and ten principal components. ") * 60
_ARTICLE_HTML = (
    "<html><head><title>GWAS of schizophrenia</title>"
    "<meta name='citation_title' content='GWAS'></head>"
    "<body><nav>Home About Login</nav>"
    f"<article><h1>Methods</h1><p>{_LONG_BODY}</p></article>"
    "<footer>Copyright 2026</footer></body></html>"
).encode("utf-8")


# ---------------------------------------------------------------------------
# Article-text extraction
# ---------------------------------------------------------------------------

class TestExtractArticleText:
    def test_prefers_article_and_strips_chrome(self):
        text = _extract_article_text(_ARTICLE_HTML.decode("utf-8"))
        assert "genotyped 130,000 individuals" in text
        assert "Login" not in text          # nav stripped
        assert "Copyright 2026" not in text  # footer outside <article>

    def test_scripts_removed(self):
        html = "<html><body><article><script>var x=1;</script><p>real text body here</p></article></body></html>"
        text = _extract_article_text(html)
        assert "real text body here" in text
        assert "var x" not in text

    def test_falls_back_to_body_when_no_article(self):
        html = "<html><body><p>plain body paragraph content</p></body></html>"
        text = _extract_article_text(html)
        assert "plain body paragraph content" in text

    def test_empty_input(self):
        assert _extract_article_text("") == ""


# ---------------------------------------------------------------------------
# fetch_landing_page_html
# ---------------------------------------------------------------------------

class TestFetchLandingPageHtml:
    @patch("biolit.fetchers.landing_page_html.requests.get")
    def test_extracts_article_text_from_landing_page(self, mock_get):
        mock_get.return_value = _mock_response(_ARTICLE_HTML)
        text = fetch_landing_page_html(doi="10.1371/journal.pone.0001234")
        assert text is not None
        assert "genotyped 130,000 individuals" in text

    @patch("biolit.fetchers.landing_page_html.requests.get")
    def test_follows_citation_fulltext_html_url(self, mock_get):
        landing = _mock_response(
            (
                "<html><head>"
                "<meta name='citation_fulltext_html_url' content='https://publisher.example.org/full'>"
                "</head><body><div id='root'></div></body></html>"
            ).encode("utf-8"),
            url="https://publisher.example.org/article",
        )
        full = _mock_response(_ARTICLE_HTML, url="https://publisher.example.org/full")
        mock_get.side_effect = [landing, full]
        text = fetch_landing_page_html(doi="10.1371/journal.pone.0001234")
        assert text is not None
        assert "genotyped 130,000 individuals" in text
        # Second GET targets the advertised full-HTML URL.
        assert mock_get.call_args_list[1].args[0] == "https://publisher.example.org/full"

    @patch("biolit.fetchers.landing_page_html.requests.get")
    def test_biorxiv_doi_skipped_without_network(self, mock_get):
        assert fetch_landing_page_html(doi="10.1101/2025.01.01.123456") is None
        mock_get.assert_not_called()

    @patch("biolit.fetchers.landing_page_html.requests.get")
    def test_bot_challenge_returns_none(self, mock_get):
        mock_get.return_value = _mock_response(
            b"<html><head><title>Just a moment...</title></head><body></body></html>"
        )
        assert fetch_landing_page_html(doi="10.1038/abc") is None

    @patch("biolit.fetchers.landing_page_html.requests.get")
    def test_js_shell_returns_none(self, mock_get):
        mock_get.return_value = _mock_response(
            b"<html><body><div id='root'></div></body></html>"
        )
        assert fetch_landing_page_html(doi="10.1038/abc") is None

    @patch("biolit.fetchers.landing_page_html.requests.get")
    def test_network_error_returns_none(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")
        assert fetch_landing_page_html(doi="10.1038/abc") is None

    def test_no_doi_or_url_returns_none(self):
        assert fetch_landing_page_html() is None
        assert fetch_landing_page_html(doi="", url="") is None

    @patch("biolit.fetchers.landing_page_html.requests.get")
    def test_custom_user_agent_is_sent(self, mock_get):
        mock_get.return_value = _mock_response(_ARTICLE_HTML)
        fetch_landing_page_html(doi="10.1038/abc", user_agent="my-agent/1.0")
        assert mock_get.call_args.kwargs["headers"]["User-Agent"] == "my-agent/1.0"

    @patch("biolit.fetchers.landing_page_html.requests.get")
    def test_url_used_when_no_doi(self, mock_get):
        mock_get.return_value = _mock_response(_ARTICLE_HTML)
        fetch_landing_page_html(url="https://publisher.example.org/article")
        assert mock_get.call_args_list[0].args[0] == "https://publisher.example.org/article"


# ---------------------------------------------------------------------------
# classify_landing_page
# ---------------------------------------------------------------------------

class TestClassifyLandingPage:
    @patch("biolit.fetchers.landing_page_html.requests.get")
    def test_bot_blocked(self, mock_get):
        mock_get.return_value = _mock_response(
            b"<html><head><title>Just a moment...</title></head></html>"
        )
        assert classify_landing_page(doi="10.1038/abc") == "bot_blocked"

    @patch("biolit.fetchers.landing_page_html.requests.get")
    def test_js_shell(self, mock_get):
        mock_get.return_value = _mock_response(
            b"<html><body><div id='root'></div></body></html>"
        )
        assert classify_landing_page(doi="10.1038/abc") == "js_shell"

    @patch("biolit.fetchers.landing_page_html.requests.get")
    def test_abstract_url(self, mock_get):
        mock_get.return_value = _mock_response(
            b"<html><body><div id='root'></div></body></html>",
            url="https://pubmed.ncbi.nlm.nih.gov/41795042/",
        )
        assert classify_landing_page(url="https://pubmed.ncbi.nlm.nih.gov/41795042/") == "abstract"

    @patch("biolit.fetchers.landing_page_html.requests.get")
    def test_fulltext(self, mock_get):
        mock_get.return_value = _mock_response(_ARTICLE_HTML)
        assert classify_landing_page(doi="10.1038/abc") == "fulltext"

    @patch("biolit.fetchers.landing_page_html.requests.get")
    def test_network_error_defaults_to_abstract(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")
        assert classify_landing_page(doi="10.1038/abc") == "abstract"

    def test_no_id_defaults_to_abstract(self):
        assert classify_landing_page() == "abstract"

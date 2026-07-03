"""Tests for the publisher landing-page PDF scraper."""
from unittest.mock import MagicMock, patch

from biolit.fetchers.landing_page import (
    fetch_via_landing_page,
    _pdf_candidates,
    _is_blocked,
)

try:
    from lxml import html as _lxml_html
except Exception:  # pragma: no cover
    _lxml_html = None


FAKE_PDF = b"%PDF-1.7 landing-page copy"


def _mock_response(status_code: int, content: bytes = b"", url: str = "https://publisher.example.org/article") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.url = url
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


# ---------------------------------------------------------------------------
# Candidate extraction / ordering
# ---------------------------------------------------------------------------

class TestPdfCandidates:
    def _doc(self, html: str):
        return _lxml_html.fromstring(html.encode("utf-8"))

    def test_citation_pdf_url_first(self):
        html = """
        <html><head>
          <meta name="citation_pdf_url" content="https://publisher.example.org/a.pdf">
          <link rel="alternate" type="application/pdf" href="/alt.pdf">
          <a href="/other.pdf">pdf</a>
        </head></html>
        """
        urls = _pdf_candidates(self._doc(html), "https://publisher.example.org/article")
        assert urls[0] == "https://publisher.example.org/a.pdf"

    def test_fallback_ordering_when_no_citation_meta(self):
        # No citation_pdf_url → link[alternate] beats the OG meta beats the anchor.
        html = """
        <html><head>
          <link rel="alternate" type="application/pdf" href="/alt.pdf">
          <meta property="og:pdf_url" content="/og.pdf">
          <a href="/plain.pdf">download</a>
        </head></html>
        """
        urls = _pdf_candidates(self._doc(html), "https://publisher.example.org/article")
        assert urls == [
            "https://publisher.example.org/alt.pdf",
            "https://publisher.example.org/og.pdf",
            "https://publisher.example.org/plain.pdf",
        ]

    def test_relative_urls_resolved_against_base(self):
        html = '<html><head><meta name="citation_pdf_url" content="files/x.pdf"></head></html>'
        urls = _pdf_candidates(self._doc(html), "https://publisher.example.org/journal/article/")
        assert urls == ["https://publisher.example.org/journal/article/files/x.pdf"]

    def test_no_candidates_returns_empty(self):
        html = "<html><head><title>no pdf here</title></head><body></body></html>"
        assert _pdf_candidates(self._doc(html), "https://publisher.example.org/article") == []


# ---------------------------------------------------------------------------
# Preprint-skip
# ---------------------------------------------------------------------------

class TestIsBlocked:
    def test_biorxiv_doi_prefix_blocked(self):
        assert _is_blocked("https://doi.org/10.1101/2025.01.01.123456", "10.1101/2025.01.01.123456")

    def test_biorxiv_host_blocked(self):
        assert _is_blocked("https://www.biorxiv.org/content/x", None)
        assert _is_blocked("https://medrxiv.org/content/x", None)

    def test_normal_publisher_not_blocked(self):
        assert not _is_blocked("https://publisher.example.org/article", "10.1038/abc")


# ---------------------------------------------------------------------------
# End-to-end fetch
# ---------------------------------------------------------------------------

class TestFetchViaLandingPage:
    @patch("biolit.fetchers.landing_page.requests.get")
    def test_citation_pdf_url_yields_pdf_bytes(self, mock_get):
        landing = _mock_response(
            200,
            content=b'<html><head><meta name="citation_pdf_url" content="https://publisher.example.org/full.pdf"></head></html>',
            url="https://publisher.example.org/article",
        )
        pdf = _mock_response(200, content=FAKE_PDF)
        mock_get.side_effect = [landing, pdf]
        assert fetch_via_landing_page(doi="10.1038/abc") == FAKE_PDF
        # Second request goes to the advertised PDF URL.
        assert mock_get.call_args_list[1].args[0] == "https://publisher.example.org/full.pdf"

    @patch("biolit.fetchers.landing_page.requests.get")
    def test_link_alternate_fallback_when_no_citation_meta(self, mock_get):
        landing = _mock_response(
            200,
            content=b'<html><head><link rel="alternate" type="application/pdf" href="/alt.pdf"></head></html>',
            url="https://publisher.example.org/article",
        )
        pdf = _mock_response(200, content=FAKE_PDF)
        mock_get.side_effect = [landing, pdf]
        assert fetch_via_landing_page(doi="10.1038/abc") == FAKE_PDF
        assert mock_get.call_args_list[1].args[0] == "https://publisher.example.org/alt.pdf"

    @patch("biolit.fetchers.landing_page.requests.get")
    def test_skips_html_candidate_and_tries_next(self, mock_get):
        landing = _mock_response(
            200,
            content=(
                b'<html><head>'
                b'<meta name="citation_pdf_url" content="/decoy.pdf">'
                b'<a href="/real.pdf">x</a>'
                b'</head></html>'
            ),
            url="https://publisher.example.org/article",
        )
        decoy = _mock_response(200, content=b"<html>not a pdf</html>")
        real = _mock_response(200, content=FAKE_PDF)
        mock_get.side_effect = [landing, decoy, real]
        assert fetch_via_landing_page(doi="10.1038/abc") == FAKE_PDF

    @patch("biolit.fetchers.landing_page.requests.get")
    def test_meta_refresh_is_followed(self, mock_get):
        first = _mock_response(
            200,
            content=b'<html><head><meta http-equiv="refresh" content="0; url=https://publisher.example.org/real-article"></head></html>',
            url="https://publisher.example.org/interstitial",
        )
        real = _mock_response(
            200,
            content=b'<html><head><meta name="citation_pdf_url" content="https://publisher.example.org/x.pdf"></head></html>',
            url="https://publisher.example.org/real-article",
        )
        pdf = _mock_response(200, content=FAKE_PDF)
        mock_get.side_effect = [first, real, pdf]
        assert fetch_via_landing_page(doi="10.1038/abc") == FAKE_PDF
        assert mock_get.call_args_list[1].args[0] == "https://publisher.example.org/real-article"

    @patch("biolit.fetchers.landing_page.requests.get")
    def test_doi_redirecting_straight_to_pdf(self, mock_get):
        mock_get.return_value = _mock_response(200, content=FAKE_PDF)
        assert fetch_via_landing_page(doi="10.1038/abc") == FAKE_PDF

    @patch("biolit.fetchers.landing_page.requests.get")
    def test_biorxiv_doi_is_skipped_without_network(self, mock_get):
        assert fetch_via_landing_page(doi="10.1101/2025.01.01.123456") is None
        mock_get.assert_not_called()

    @patch("biolit.fetchers.landing_page.requests.get")
    def test_returns_none_when_no_pdf_found(self, mock_get):
        mock_get.return_value = _mock_response(
            200, content=b"<html><head><title>no pdf</title></head></html>"
        )
        assert fetch_via_landing_page(doi="10.1038/abc") is None

    @patch("biolit.fetchers.landing_page.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = ConnectionError("timeout")
        assert fetch_via_landing_page(doi="10.1038/abc") is None

    def test_returns_none_without_doi_or_url(self):
        assert fetch_via_landing_page() is None
        assert fetch_via_landing_page(doi="", url="") is None

    @patch("biolit.fetchers.landing_page.requests.get")
    def test_custom_user_agent_is_sent(self, mock_get):
        mock_get.return_value = _mock_response(200, content=FAKE_PDF)
        fetch_via_landing_page(doi="10.1038/abc", user_agent="my-agent/1.0")
        assert mock_get.call_args.kwargs["headers"]["User-Agent"] == "my-agent/1.0"

    @patch("biolit.fetchers.landing_page.requests.get")
    def test_user_agent_env_override(self, mock_get, monkeypatch):
        monkeypatch.setenv("BIOLIT_LANDING_USER_AGENT", "env-agent/2.0")
        mock_get.return_value = _mock_response(200, content=FAKE_PDF)
        fetch_via_landing_page(doi="10.1038/abc")
        assert mock_get.call_args.kwargs["headers"]["User-Agent"] == "env-agent/2.0"

    @patch("biolit.fetchers.landing_page.requests.get")
    def test_url_used_when_no_doi(self, mock_get):
        mock_get.return_value = _mock_response(200, content=FAKE_PDF)
        fetch_via_landing_page(url="https://publisher.example.org/article")
        assert mock_get.call_args_list[0].args[0] == "https://publisher.example.org/article"

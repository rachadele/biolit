"""Tests for the landing-page status-classification heuristics."""
from biolit.fetchers.page_classify import (
    STATUS_ABSTRACT,
    STATUS_BOT_BLOCKED,
    STATUS_FULLTEXT,
    STATUS_JS_SHELL,
    classify_html_status,
    has_substantial_content,
    is_abstract_only_url,
    is_bot_challenge,
    visible_char_count,
)


# ---------------------------------------------------------------------------
# is_bot_challenge
# ---------------------------------------------------------------------------

class TestIsBotChallenge:
    def test_cloudflare_just_a_moment(self):
        html = "<html><head><title>Just a moment...</title></head><body></body></html>"
        assert is_bot_challenge(html)

    def test_cloudflare_challenge_platform(self):
        html = '<html><body><script src="/cdn-cgi/challenge-platform/h/b/orchestrate"></script></body></html>'
        assert is_bot_challenge(html)

    def test_cf_chl_token(self):
        assert is_bot_challenge("<html>window.__cf_chl_opt = {};</html>")

    def test_attention_required(self):
        assert is_bot_challenge("<html><head><title>Attention Required! | Cloudflare</title></head></html>")

    def test_real_article_not_flagged(self):
        html = (
            "<html><head><title>A study of synaptic genes in schizophrenia</title></head>"
            "<body><article><p>We performed a GWAS in 50,000 cases.</p></article></body></html>"
        )
        assert not is_bot_challenge(html)

    def test_empty_is_false(self):
        assert not is_bot_challenge("")
        assert not is_bot_challenge(None)


# ---------------------------------------------------------------------------
# has_substantial_content / visible_char_count
# ---------------------------------------------------------------------------

class TestHasSubstantialContent:
    def test_js_shell_below_threshold(self):
        # A near-empty SPA shell: lots of script, little visible text.
        shell = (
            "<html><head><script>var x = " + ("1," * 5000) + "</script></head>"
            "<body><div id='root'></div><noscript>Enable JS</noscript></body></html>"
        )
        assert not has_substantial_content(shell)
        assert classify_html_status(shell) == STATUS_JS_SHELL

    def test_real_article_above_threshold(self):
        body = "<p>" + ("We genotyped many individuals and ran association tests. " * 200) + "</p>"
        html = f"<html><body><article>{body}</article></body></html>"
        assert has_substantial_content(html)

    def test_scripts_and_styles_excluded_from_count(self):
        html = "<html><head><style>" + ("a{color:red}" * 1000) + "</style></head><body>hi</body></html>"
        # Only "hi" counts as visible.
        assert visible_char_count(html) < 10

    def test_explicit_threshold_override(self):
        html = "<html><body><p>short text here</p></body></html>"
        assert has_substantial_content(html, threshold=5)
        assert not has_substantial_content(html, threshold=100000)

    def test_env_threshold_override(self, monkeypatch):
        monkeypatch.setenv("BIOLIT_JS_SHELL_CHAR_THRESHOLD", "5")
        html = "<html><body><p>short text here</p></body></html>"
        assert has_substantial_content(html)


# ---------------------------------------------------------------------------
# is_abstract_only_url
# ---------------------------------------------------------------------------

class TestIsAbstractOnlyUrl:
    def test_pubmed_is_abstract_only(self):
        assert is_abstract_only_url("https://pubmed.ncbi.nlm.nih.gov/41795042/")

    def test_lww_abstract_path(self):
        assert is_abstract_only_url("https://journals.lww.com/pain/abstract/2024/01000/x.5.aspx")

    def test_oxford_article_abstract(self):
        assert is_abstract_only_url("https://academic.oup.com/journal/article-abstract/12/3/456")

    def test_full_text_url_not_abstract(self):
        assert not is_abstract_only_url("https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0001234")

    def test_none_and_empty(self):
        assert not is_abstract_only_url(None)
        assert not is_abstract_only_url("")


# ---------------------------------------------------------------------------
# classify_html_status precedence
# ---------------------------------------------------------------------------

class TestClassifyHtmlStatus:
    def test_bot_challenge_wins_over_everything(self):
        html = "<html><head><title>Just a moment...</title></head></html>"
        assert classify_html_status(html, "https://pubmed.ncbi.nlm.nih.gov/1/") == STATUS_BOT_BLOCKED

    def test_abstract_url_before_js_shell(self):
        thin = "<html><body><div id='root'></div></body></html>"
        assert classify_html_status(thin, "https://pubmed.ncbi.nlm.nih.gov/1/") == STATUS_ABSTRACT

    def test_js_shell_when_thin_and_no_abstract_url(self):
        thin = "<html><body><div id='root'></div></body></html>"
        assert classify_html_status(thin, "https://publisher.example.org/article") == STATUS_JS_SHELL

    def test_fulltext_when_substantial(self):
        body = "<p>" + ("Detailed methods and results. " * 200) + "</p>"
        html = f"<html><body><article>{body}</article></body></html>"
        assert classify_html_status(html, "https://publisher.example.org/article") == STATUS_FULLTEXT

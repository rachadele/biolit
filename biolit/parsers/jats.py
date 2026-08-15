"""Parse JATS XML (from PMC or preprint servers) into named sections.

Uses lxml for robust XML handling. Falls back to stdlib ElementTree when
lxml is unavailable, at the cost of namespace handling fidelity.
"""
import re

# JATS block-level elements at whose boundaries we insert ``\n`` during
# text extraction. Inline elements (``sup``, ``sub``, ``italic``,
# ``bold``, ``ext-link``, ``xref``, ``inline-formula``, …) are
# deliberately excluded so compound terms with markup mid-token —
# ``Foxp3<sup>creYFP</sup>Mice``, ``HDAC6<sup>KO</sup>``,
# ``mtND6<sup>mut</sup>`` — stay glued in the output.
_JATS_BLOCK_TAGS = frozenset({
    "sec", "title", "label", "p", "list", "list-item",
    "table-wrap", "caption", "fig", "boxed-text",
    "disp-formula", "disp-quote", "abstract", "body",
    "front", "back", "ref-list", "ref", "table", "tr",
    "thead", "tbody", "th", "td", "fn", "notes",
})


def _local_name(tag) -> str:
    """Strip XML namespace prefix from a tag name (``{ns}sec`` → ``sec``)."""
    if not tag or not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag.split("}", 1)[-1]
    return tag


def _format_text(parts: list[str]) -> str:
    """Join parts and collapse runs of 3+ newlines to ``\n\n``."""
    return re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()


try:
    from lxml import etree  # type: ignore[import]

    def _text_of(element) -> str:
        """Walk element + descendants, emitting text with ``\n`` at
        block-level boundaries but no separator at inline boundaries.
        Replaces a previous ``''.join(itertext())`` that glued every
        block boundary together (e.g. ``MethodsContact``,
        ``DetailsMice``, ``MiceWe``)."""
        parts: list[str] = []

        def walk(e) -> None:
            is_block = _local_name(e.tag) in _JATS_BLOCK_TAGS
            if is_block:
                parts.append("\n")
            if e.text:
                parts.append(e.text)
            for child in e:
                walk(child)
                if child.tail:
                    parts.append(child.tail)
            if is_block:
                parts.append("\n")

        walk(element)
        return _format_text(parts)

    def _parse_xml(xml_bytes: bytes):
        parser = etree.XMLParser(recover=True, remove_comments=True)
        return etree.fromstring(xml_bytes, parser=parser)

    def _find_all(root, xpath: str):
        # Strip namespace from elements for simplicity — use local-name()
        return root.xpath(xpath)

except ImportError:
    import xml.etree.ElementTree as ET

    def _text_of(element) -> str:  # type: ignore[misc]
        """ET fallback: same block-aware traversal as the lxml path."""
        parts: list[str] = []

        def walk(e) -> None:
            is_block = _local_name(e.tag) in _JATS_BLOCK_TAGS
            if is_block:
                parts.append("\n")
            if e.text:
                parts.append(e.text)
            for child in e:
                walk(child)
                if child.tail:
                    parts.append(child.tail)
            if is_block:
                parts.append("\n")

        walk(element)
        return _format_text(parts)

    def _parse_xml(xml_bytes: bytes):  # type: ignore[misc]
        return ET.fromstring(xml_bytes)

    def _find_all(root, xpath: str):  # type: ignore[misc]
        # Simplified fallback — only used when lxml absent
        return root.findall(".//" + xpath.lstrip("./").split("[")[0])


# Section headings we care about (order matters for output)
KNOWN_SECTIONS = [
    "abstract",
    "introduction",
    "background",
    "methods",
    "materials and methods",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
]

_HEADING_RE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in KNOWN_SECTIONS) + r")\b",
    re.IGNORECASE,
)


def _normalise_heading(text: str) -> str:
    m = _HEADING_RE.search(text)
    if m:
        return m.group(1).lower()
    return text.lower().strip()


def parse_jats_sections(xml_bytes: bytes) -> dict[str, str]:
    """Parse JATS XML and return a dict mapping section name → plain text.

    Recognised section names: abstract, introduction, background, methods,
    materials and methods, results, discussion, conclusion/conclusions.
    Unknown sections are included under their title text as the key.
    """
    try:
        root = _parse_xml(xml_bytes)
    except Exception:
        return {}

    sections: dict[str, str] = {}

    # 1. Explicit <abstract> element (always present for PMC records)
    try:
        abstracts = root.findall(".//{*}abstract") if hasattr(root, "findall") else []
        if not abstracts:  # lxml path
            try:
                abstracts = root.xpath("//*[local-name()='abstract']")
            except Exception:
                abstracts = []
        for ab in abstracts:
            text = _text_of(ab)
            if text:
                sections["abstract"] = text
                break
    except Exception:
        pass

    # 2. Walk TOP-LEVEL <sec> elements and key them by their <title>.
    #
    # Only sections that are NOT nested inside another <sec>. A parent
    # section's ``_text_of`` already includes all of its descendants'
    # text, so also emitting each child <sec> (e.g. Methods > "Mice",
    # "Flow cytometry") as its own top-level key DUPLICATES the Methods
    # content. Under downstream truncation that duplication is harmful:
    # the same Methods text occupies the excerpt twice and pushes later
    # sections past the cap (2026-07-31: a 35 KB immunology paper's
    # Methods appeared as both ``methods`` and a dozen subsection keys;
    # the "Mice" block with the animal age survived only because it was
    # also inside ``methods``). Emitting top-level sections only keeps
    # ``methods`` intact with its full nested content while dropping the
    # redundant per-subsection copies. Genuinely flat layouts (each
    # Methods subsection a top-level <sec> with no parent wrapper) are
    # unaffected — those secs have no <sec> ancestor and are all kept.
    # A STRUCTURED ABSTRACT's subsections are <sec> too, and they live inside
    # <abstract> rather than inside another <sec> — so "no <sec> ancestor" lets
    # them through as if they were body sections. They then come FIRST in
    # document order and claim the ``methods`` / ``results`` keys, and the
    # ``key not in sections`` guard below silently drops the body's real
    # sections. Introduction / Discussion survive only because no abstract has
    # subsections by those names.
    #
    # Measured on PMC4235044 (Mol Vis; abstract = Purpose/Methods/Results/
    # Conclusions): ``methods`` came back as 125 characters of abstract instead
    # of the paper's 7,196-character Methods, so the animal age — "Retinas were
    # dissected from mice 48 to 120 days old" — was absent from the parse. Any
    # consumer selecting sections_wanted=["methods", ...] therefore received a
    # few hundred characters of abstract LABELLED as Methods, which is worse
    # than an empty result: it reads as a successful full-text parse.
    #
    # The abstract itself is not lost — step 1 above already captured it whole
    # under the ``abstract`` key.
    try:
        try:
            sec_elements = root.xpath(
                "//*[local-name()='sec']"
                "[not(ancestor::*[local-name()='sec'])]"
                "[not(ancestor::*[local-name()='abstract'])]"
            )
        except AttributeError:
            all_secs = root.findall(".//{*}sec")
            _parent = {child: parent for parent in root.iter() for child in parent}

            def _has_excluded_ancestor(el) -> bool:
                """Nested in another <sec> (parent already carries the text) or
                inside <abstract> (a structured-abstract subsection)."""
                p = _parent.get(el)
                while p is not None:
                    tag = getattr(p, "tag", "")
                    if isinstance(tag, str) and (
                        tag in ("sec", "abstract")
                        or tag.endswith("}sec")
                        or tag.endswith("}abstract")
                    ):
                        return True
                    p = _parent.get(p)
                return False

            sec_elements = [s for s in all_secs if not _has_excluded_ancestor(s)]
    except Exception:
        sec_elements = []

    for sec in sec_elements:
        try:
            try:
                title_elem = sec.xpath("*[local-name()='title']")
                title = _text_of(title_elem[0]) if title_elem else ""
            except AttributeError:
                title_elem = sec.find("{*}title")
                title = _text_of(title_elem) if title_elem is not None else ""
        except Exception:
            title = ""

        key = _normalise_heading(title) if title else "body"
        text = _text_of(sec)
        if text and key not in sections:
            sections[key] = text

    # 3. <fn> elements — data-availability/accession statements are often
    # published as footnotes (e.g. in <back>/<fn-group>) rather than in a
    # body <sec>, and were previously silently dropped. Footnotes are
    # never nested inside each other, so no ancestor-filtering is needed.
    try:
        try:
            fn_elements = root.xpath("//*[local-name()='fn']")
        except AttributeError:
            fn_elements = root.findall(".//{*}fn")
    except Exception:
        fn_elements = []

    fn_text = "\n".join(t for fn in fn_elements if (t := _text_of(fn)))
    if fn_text:
        sections["footnotes"] = fn_text

    # 4. <notes> elements — same rationale as <fn>: data-availability,
    # funding, and ethics statements are often published as <notes>
    # (usually under <back>) rather than in a body <sec>.
    try:
        try:
            notes_elements = root.xpath("//*[local-name()='notes']")
        except AttributeError:
            notes_elements = root.findall(".//{*}notes")
    except Exception:
        notes_elements = []

    notes_text = "\n".join(t for n in notes_elements if (t := _text_of(n)))
    if notes_text:
        sections["notes"] = notes_text

    return sections



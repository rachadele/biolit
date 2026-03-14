"""Parse JATS XML (from PMC or preprint servers) into named sections.

Uses lxml for robust XML handling. Falls back to stdlib ElementTree when
lxml is unavailable, at the cost of namespace handling fidelity.
"""
import re

try:
    from lxml import etree  # type: ignore[import]

    def _text_of(element) -> str:
        """Extract all text content from an lxml element, stripping tags."""
        return "".join(element.itertext()).strip()

    def _parse_xml(xml_bytes: bytes):
        parser = etree.XMLParser(recover=True, remove_comments=True)
        return etree.fromstring(xml_bytes, parser=parser)

    def _find_all(root, xpath: str):
        # Strip namespace from elements for simplicity — use local-name()
        return root.xpath(xpath)

except ImportError:
    import xml.etree.ElementTree as ET

    def _text_of(element) -> str:  # type: ignore[misc]
        texts = []
        for node in element.iter():
            if node.text:
                texts.append(node.text)
            if node.tail:
                texts.append(node.tail)
        return " ".join(texts).strip()

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

    # 2. Walk <sec> elements and key them by their <title>
    try:
        try:
            sec_elements = root.xpath("//*[local-name()='sec']")
        except AttributeError:
            sec_elements = root.findall(".//{*}sec")
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

    return sections



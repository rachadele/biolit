"""Extract text from PDFs and split into sections heuristically.

Uses pdfminer.six for text extraction. Falls back gracefully when the library
is unavailable or the PDF cannot be parsed (e.g. scanned documents).
"""
import io
import re

# Section headings we look for (same list as jats.py)
_KNOWN_HEADINGS = [
    "abstract",
    "introduction",
    "background",
    "methods",
    "materials and methods",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "references",
    "acknowledgements",
    "acknowledgments",
    "supplementary",
]

# A heading line: short (≤80 chars), optionally numbered, title-case or ALL-CAPS
_HEADING_LINE_RE = re.compile(
    r"^\s*(?:\d+[.\s]+)?("
    + "|".join(re.escape(h) for h in _KNOWN_HEADINGS)
    + r"s?)\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _extract_text(pdf_bytes: bytes) -> str:
    """Use pdfminer.six to extract raw text from PDF bytes."""
    try:
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams

        out = io.StringIO()
        extract_text_to_fp(io.BytesIO(pdf_bytes), out, laparams=LAParams())
        return out.getvalue()
    except ImportError:
        raise ImportError("Install pdfminer.six: pip install pdfminer.six")
    except Exception:
        return ""


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Public: raw extracted text from PDF bytes (``""`` on failure).

    Thin wrapper over the internal extractor for callers that want the
    whole document as one string rather than heuristic sections — e.g.
    the supplementary-materials fetcher mining a supplementary-methods
    PDF. Re-raises ImportError so a missing pdfminer.six stays loud."""
    try:
        return _extract_text(pdf_bytes)
    except ImportError:
        raise
    except Exception:
        return ""


def parse_pdf_sections(pdf_bytes: bytes) -> dict[str, str]:
    """Extract text from *pdf_bytes* and split by section heading.

    Returns a dict mapping (lowercase) section name → text. If section
    detection yields nothing, returns ``{"body": <full text>}``.
    """
    try:
        raw = _extract_text(pdf_bytes)
    except ImportError:
        raise
    except Exception:
        return {}

    if not raw.strip():
        return {}

    # Find all heading matches
    matches = list(_HEADING_LINE_RE.finditer(raw))
    if not matches:
        return {"body": raw.strip()}

    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        heading = match.group(1).lower().strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        text = raw[start:end].strip()
        if text and heading not in sections:
            sections[heading] = text

    # Capture any text before the first heading as a preamble
    preamble = raw[: matches[0].start()].strip()
    if preamble:
        sections.setdefault("preamble", preamble)

    return sections



"""Extract text from DOCX (Office Open XML) documents.

Journals frequently ship supplementary methods as ``.docx``. python-docx
is lazy-imported so biolit still imports if the optional dependency is
absent (the supplementary fetcher then simply returns no text for docx
files rather than erroring).
"""
import io


def extract_docx_text(docx_bytes: bytes) -> str:
    """Return paragraph + table text of a ``.docx``, or ``""`` on failure
    / when python-docx is unavailable.

    Table rows are flattened to tab-joined cells so a supplementary
    methods table (reagents, strains, antibodies) stays readable as text.
    """
    try:
        import docx  # python-docx
    except ImportError:
        return ""
    try:
        document = docx.Document(io.BytesIO(docx_bytes))
    except Exception:
        return ""
    parts: list[str] = [
        p.text.strip() for p in document.paragraphs if p.text and p.text.strip()
    ]
    for table in getattr(document, "tables", []):
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
            if cells:
                parts.append("\t".join(cells))
    return "\n".join(parts).strip()

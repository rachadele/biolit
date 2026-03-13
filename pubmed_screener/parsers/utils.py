"""Shared parser utilities."""

DEFAULT_MAX_CHARS = 12_000  # ~3 000 tokens — safe for most models


def select_sections(
    sections: dict[str, str],
    wanted: list[str] | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Concatenate *wanted* sections from *sections* up to *max_chars*.

    If *wanted* is None or empty, all sections are included.
    Sections are joined with a labelled header line for readability.
    """
    if not sections:
        return ""

    if wanted:
        # Prefer exact matches; fall back to prefix / substring matches
        chosen = {}
        for key, text in sections.items():
            for w in wanted:
                if w.lower() in key.lower():
                    chosen[key] = text
                    break
        if not chosen:
            chosen = sections
    else:
        chosen = sections

    parts = []
    total = 0
    for key, text in chosen.items():
        header = f"=== {key.upper()} ===\n"
        chunk = header + text + "\n"
        if total + len(chunk) > max_chars:
            remaining = max_chars - total - len(header)
            if remaining > 0:
                parts.append(header + text[:remaining] + " [truncated]")
            break
        parts.append(chunk)
        total += len(chunk)

    return "\n".join(parts)


"""Shared parser utilities."""

DEFAULT_MAX_TOKENS = 12_500  # ~50 000 chars at ~4 chars/token


def select_sections(
    sections: dict[str, str],
    wanted: list[str] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """Concatenate *wanted* sections from *sections* up to *max_tokens*.

    If *wanted* is None or empty, all sections are included.
    Sections are joined with a labelled header line for readability.
    Token count is approximated as len(text) // 4.
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

    max_chars = max_tokens * 4
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

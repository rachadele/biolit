"""Shared parser utilities."""

DEFAULT_MAX_TOKENS = 12_500  # ~50 000 chars at ~4 chars/token

#: Key for the section holding <table-wrap> elements the document hoisted out
#: of its body (PMC <floats-group>). Defined HERE, beside the registry below,
#: and imported by the parser — the two were separate string literals once, and
#: renaming one would have silently un-registered the section and re-armed the
#: fallback bug the registry exists to prevent.
#:
#: It must contain a substring some caller passes as `wanted` (curation callers
#: pass "methods"), or `select_sections` drops it and the collector is inert.
TABLES_SECTION_KEY = "methods: tables"

#: Section keys the PARSER synthesizes rather than reading off a heading in the
#: document. They participate in selection normally, but must not satisfy the
#: "did we recognize any of this paper's headings?" test in `select_sections` —
#: see the comment there. Any future synthesized section belongs here.
SYNTHETIC_SECTION_KEYS = frozenset({TABLES_SECTION_KEY})


def select_sections(
    sections: dict[str, str],
    wanted: list[str] | None = None,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
) -> str:
    """Concatenate *wanted* sections from *sections* up to *max_tokens*.

    If *wanted* is None or empty, all sections are included.
    Sections are joined with a labelled header line for readability.
    Token count is approximated as len(text) // 4.

    ``max_tokens=None`` (or 0) disables the budget entirely. **Use it for any
    consumer that is not an LLM.** The cap exists to bound a prompt — the
    ``mcp_server`` docstring calls it *"maximum tokens of paper text sent to
    the LLM"* — so applying it to a string search (does this paper mention
    ``GSE123945``?) silently answers "no" for a paper that says yes further
    down. That is a category error rather than a tuning problem: a grep has no
    context window, and the caller cannot tell a real absence from a budgeted
    one.
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
        # 🛑 The fallback asks "did we recognize any of THIS PAPER'S headings?"
        # If none matched, filtering would throw the paper away, so hand back
        # everything instead. A SYNTHESIZED section is not evidence that
        # heading recognition worked: the parser keys a hoisted <table-wrap>
        # itself, so a paper whose headings are all unrecognized (Cell Press
        # "Experimental Procedures", say) would suddenly "match", switch the
        # safety net off, and be reduced to its tables.
        #
        # Measured on PMC before this guard existed: PMC7614342 went 38,204 ->
        # 2,821 selected chars, a 93% loss, and 18 of 207 sampled papers (8.7%)
        # depend on this fallback.
        if not any(k not in SYNTHETIC_SECTION_KEYS for k in chosen):
            chosen = sections
    else:
        chosen = sections

    # Promote small back-matter sections (data availability / notes /
    # footnotes / supplementary material) to the front so they survive
    # truncation even when earlier narrative sections consume the budget.
    # Some journals (e.g. Disease Models & Mechanisms) place Methods last,
    # after Results/Discussion, so it also needs promoting: "method" matches
    # both the "methods" and "materials and methods" JATS section keys
    # without matching any other recognised section name.
    priority_markers = (
        "data availability",
        "notes",
        "footnote",
        "supplementary material",
        "method",
    )
    priority_items = []
    rest_items = []
    for key, text in chosen.items():
        if any(marker in key.lower() for marker in priority_markers):
            priority_items.append((key, text))
        else:
            rest_items.append((key, text))
    ordered_items = priority_items + rest_items

    # No budget: hand back everything. Used by consumers that are not an LLM
    # — see the ``max_tokens`` note in the docstring.
    if not max_tokens:
        return "\n".join(f"=== {k.upper()} ===\n" + t + "\n"
                         for k, t in ordered_items)

    max_chars = max_tokens * 4
    kept: dict[str, str] = {}
    total = 0

    # Pass 1 — everything that fits WHOLE, in promoted order.
    #
    # 🛑 Skip, do not stop. This loop used to ``break`` on the first section
    # that overflowed, which dropped every LATER section whatever its size —
    # so one long narrative section made a 200-character back-matter section
    # unreachable even though the budget had room for it. Promotion (above)
    # protects the sections we can name; this protects the ones we cannot.
    for key, text in ordered_items:
        chunk = f"=== {key.upper()} ===\n" + text + "\n"
        if total + len(chunk) <= max_chars:
            kept[key] = text
            total += len(chunk)

    # Pass 2 — spend whatever is left truncating the first section still
    # omitted. Deferring truncation to a second pass is what makes pass 1
    # safe: filling the tail greedily would consume the budget an
    # already-fitting later section needed.
    for key, text in ordered_items:
        if key in kept:
            continue
        header = f"=== {key.upper()} ===\n"
        remaining = max_chars - total - len(header) - len(" [truncated]\n")
        # ``not kept`` is the floor — never return nothing when there is
        # content and a budget; a caller asking for 25 tokens wants its 25.
        if remaining > 500 or not kept:
            kept[key] = text[:max(remaining, 0)] + " [truncated]"
        break

    return "\n".join(f"=== {k.upper()} ===\n" + kept[k] + "\n"
                     for k, _ in ordered_items if k in kept)

#!/usr/bin/env python3
"""
PubMed Alert Literature Screener — entry point shim.

The implementation lives in the `pubmed_screener` package.
Run this file directly or use `python -m pubmed_screener.cli`.

Usage examples:
    python screen.py pubmed.eml
    python screen.py pubmed.eml --default
    python screen.py pubmed.eml --fulltext --provider openai --model gpt-4o-mini
    python screen.py pubmed.eml --provider ollama --model llama3
    python screen.py pubmed.eml --criterion "Is this about X?" --fields "method, summary"
    python screen.py pubmed.eml --fulltext --unpaywall-email you@example.com --sections "methods,results"
    python screen.py pubmed.eml --output results.csv
"""
from pubmed_screener.cli import main

if __name__ == "__main__":
    main()

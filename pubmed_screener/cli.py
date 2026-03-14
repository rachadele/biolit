"""CLI entry point for pubmed-screener."""
import argparse
import os
import sys

from dotenv import load_dotenv

from pubmed_screener.llm import get_llm_client
from pubmed_screener.pipeline import run
from pubmed_screener.parsers.utils import DEFAULT_MAX_CHARS
from pubmed_screener.utils import read_eml_body, extract_pmids, read_pmids_file

load_dotenv()

DEFAULT_CRITERION = (
    "Is this paper SPECIFICALLY about schizophrenia AND does it use genetics "
    "or genomics methods (e.g. GWAS, WGS, scRNA-seq, proteomics, gene expression)?"
)
DEFAULT_FIELDS = "methodology, sample_type, causal_claims, genetics_claims, summary"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Screen PubMed alert emails with a configurable criterion and output fields. "
            "Supports multiple LLM providers and optional full-text fetching."
        )
    )
    parser.add_argument(
        "input_file",
        help="PubMed alert .eml file, or a plain-text file of PMIDs (one per line)",
    )

    # Screening / extraction
    parser.add_argument("--criterion", default=None,
                        help="Relevance screening criterion (yes/no question)")
    parser.add_argument("--fields", default=None,
                        help="Fields to extract (comma-separated names)")
    parser.add_argument("--default", action="store_true",
                        help="Use default schizophrenia genomics criterion and fields")
    parser.add_argument("--output", default="results.csv",
                        help="Output CSV path (default: results.csv)")

    # LLM provider
    parser.add_argument(
        "--provider", default=os.environ.get("LLM_PROVIDER", "anthropic"),
        choices=["anthropic", "openai", "ollama"],
        help="LLM provider (default: anthropic, or LLM_PROVIDER env var)",
    )
    parser.add_argument(
        "--model", default=os.environ.get("LLM_MODEL"),
        help="Model name for the chosen provider (uses provider default if omitted)",
    )
    parser.add_argument(
        "--openai-base-url", default=None,
        help="Custom base URL for OpenAI-compatible endpoints (e.g. Azure, local vLLM)",
    )
    parser.add_argument(
        "--ollama-url", default="http://localhost:11434",
        help="Ollama server URL (default: http://localhost:11434)",
    )

    # Full-text
    parser.add_argument(
        "--fulltext", action="store_true",
        help="Attempt to fetch full text from PMC, preprint servers, and Unpaywall",
    )
    parser.add_argument(
        "--unpaywall-email", default=os.environ.get("UNPAYWALL_EMAIL"),
        help="Email for Unpaywall API (required when --fulltext is used; or set UNPAYWALL_EMAIL)",
    )
    parser.add_argument(
        "--sections", default=None,
        help=(
            "Comma-separated list of sections to send to the LLM when full text is available "
            "(e.g. 'methods,results'). Default: all sections."
        ),
    )
    parser.add_argument(
        "--max-chars", type=int, default=DEFAULT_MAX_CHARS,
        help=f"Maximum characters of paper text sent to the LLM (default: {DEFAULT_MAX_CHARS})",
    )

    args = parser.parse_args(argv)

    # Resolve criterion and fields
    if args.default:
        criterion = DEFAULT_CRITERION
        fields = DEFAULT_FIELDS
    else:
        criterion = args.criterion
        if not criterion:
            criterion = input("Screening criterion (yes/no question about relevance): ").strip()
        fields = args.fields
        if not fields:
            fields = input(
                "Fields to extract (comma-separated, e.g. methodology, sample_type, summary): "
            ).strip()

    # Build LLM client
    try:
        extra: dict = {}
        if args.provider == "openai" and args.openai_base_url:
            extra["base_url"] = args.openai_base_url
        if args.provider == "ollama":
            extra["base_url"] = args.ollama_url
        client = get_llm_client(args.provider, args.model, **extra)
    except (EnvironmentError, ImportError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"Using LLM: {client}\n")

    # Resolve input to a list of PMIDs
    if args.input_file.endswith(".eml"):
        body = read_eml_body(args.input_file)
        pmids = extract_pmids(body)
        print(f"Found {len(pmids)} PMIDs in {args.input_file}\n")
    else:
        pmids = read_pmids_file(args.input_file)
        print(f"Read {len(pmids)} PMIDs from {args.input_file}\n")

    if not pmids:
        print("No PMIDs found. Exiting.")
        sys.exit(1)

    # Sections filter
    sections_wanted = (
        [s.strip() for s in args.sections.split(",") if s.strip()]
        if args.sections
        else None
    )

    run(
        client=client,
        pmids=pmids,
        criterion=criterion,
        fields_description=fields,
        output_path=args.output,
        fulltext=args.fulltext,
        unpaywall_email=args.unpaywall_email,
        sections_wanted=sections_wanted,
        max_chars=args.max_chars,
    )


if __name__ == "__main__":
    main()


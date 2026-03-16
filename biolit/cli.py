"""CLI entry point for biolit."""
import argparse
import os
import sys

from dotenv import load_dotenv

from biolit.fetchers.pubmed import doi_to_pmid
from biolit.llm import get_llm_client
from biolit.pipeline import run, run_geo, screen_by_pmid, screen_by_geo, screen_by_doi
from biolit.parsers.utils import DEFAULT_MAX_CHARS
from biolit.utils import read_eml_body, extract_pmids, read_pmids_file, read_geo_file

load_dotenv()

DEFAULT_CRITERION = (
    "Is this paper SPECIFICALLY about schizophrenia AND does it use genetics "
    "or genomics methods (e.g. GWAS, WGS, scRNA-seq, proteomics, gene expression)?"
)
DEFAULT_FIELDS = "methodology, sample_type, causal_claims, genetics_claims, summary"


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "screen":
        return _screen_main(argv[1:])
    return _run_main(argv)


def _screen_main(argv: list[str] | None = None) -> None:
    """biolit screen — quickly screen a single PMID or GEO accession."""
    import json

    parser = argparse.ArgumentParser(
        prog="biolit screen",
        description="Screen a single paper or GEO record for relevance.",
        epilog=(
            "Examples:\n"
            "  biolit screen --pmid 41627908 --default\n"
            "  biolit screen --accession GSE53987 --default\n"
            "  biolit screen --pmid 41627908 --criterion 'Is this about GWAS?'\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    id_group = parser.add_mutually_exclusive_group(required=True)
    id_group.add_argument("--pmid", help="PubMed ID to screen")
    id_group.add_argument("--doi", help="DOI to screen (resolved to PMID via NCBI)")
    id_group.add_argument("--accession", help="GEO accession to screen")
    parser.add_argument("--criterion", default=None, help="Relevance question (yes/no)")
    parser.add_argument("--default", action="store_true", help="Use schizophrenia genomics criterion")
    parser.add_argument("--fulltext", action="store_true", help="Fetch full text before screening (PMID only)")
    parser.add_argument("--unpaywall-email", default=os.environ.get("UNPAYWALL_EMAIL"))
    parser.add_argument("--provider", default=os.environ.get("LLM_PROVIDER", "anthropic"),
                        choices=["anthropic", "openai", "ollama"])
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL"))

    args = parser.parse_args(argv)

    criterion = DEFAULT_CRITERION if args.default else args.criterion
    if not criterion:
        criterion = input("Screening criterion (yes/no question about relevance): ").strip()

    try:
        client = get_llm_client(args.provider, args.model)
    except (EnvironmentError, ImportError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    if args.doi:
        pmid = doi_to_pmid(args.doi)
        if pmid:
            print(f"Resolved {args.doi} → PMID {pmid}")
            result = screen_by_pmid(client, pmid, criterion,
                                    fulltext=args.fulltext,
                                    unpaywall_email=args.unpaywall_email)
        else:
            print(f"Could not resolve {args.doi} to a PMID — fetching directly by DOI")
            result = screen_by_doi(client, args.doi, criterion,
                                   unpaywall_email=args.unpaywall_email)
    elif args.pmid:
        result = screen_by_pmid(client, args.pmid, criterion,
                                fulltext=args.fulltext,
                                unpaywall_email=args.unpaywall_email)
    else:
        result = screen_by_geo(client, args.accession, criterion)

    relevant = result.get("relevant")
    reason = result.get("reason", "")
    source = result.get("text_source", "")
    status = "RELEVANT" if relevant else "NOT RELEVANT"
    print(f"{status} [{source}] — {reason}")
    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)


def _run_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="biolit",
        description=(
            "LLM-assisted biomedical literature screening and extraction. "
            "Accepts PubMed alert emails (.eml), plain PMID lists, or GEO accession lists. "
            "Screens each record for relevance, then extracts structured fields into a CSV."
        ),
        epilog=(
            "Examples:\n"
            "  biolit alert.eml --default\n"
            "  biolit pmids.txt --default\n"
            "  biolit --pmids 41795042,41792186 --default\n"
            "  biolit geo_accessions.txt --default\n"
            "  biolit --accessions GSE53987 --default\n"
            "  biolit alert.eml --default --fulltext --unpaywall-email you@example.com\n"
            "  biolit pmids.txt --criterion 'Is this about treatment-resistant schizophrenia?' "
            "--fields 'methodology, sample_size, outcomes'\n"
            "\n"
            "Environment variables:\n"
            "  ANTHROPIC_API_KEY   Required for Anthropic provider (default)\n"
            "  OPENAI_API_KEY      Required for OpenAI provider\n"
            "  NCBI_API_KEY        Optional; increases NCBI rate limits\n"
            "  UNPAYWALL_EMAIL     Required when using --fulltext\n"
            "  LLM_PROVIDER        Default provider if --provider not set\n"
            "  LLM_MODEL           Default model if --model not set\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input_file", nargs="?", default=None,
        help="PubMed alert .eml file, plain-text file of PMIDs, or plain-text file of GEO accessions",
    )
    parser.add_argument(
        "--pmids", default=None,
        help="Comma-separated PMIDs (alternative to input_file)",
    )
    parser.add_argument(
        "--dois", default=None,
        help="Comma-separated DOIs (alternative to input_file; resolved to PMIDs via NCBI)",
    )
    parser.add_argument(
        "--accessions", default=None,
        help="Comma-separated GEO accessions (alternative to input_file)",
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

    # Resolve input — CLI flags take priority over file
    if not args.input_file and not args.pmids and not args.dois and not args.accessions:
        print("Error: provide an input_file, --pmids, --dois, or --accessions.")
        sys.exit(1)

    if args.accessions:
        input_type = "geo"
        accessions = [a.strip() for a in args.accessions.split(",") if a.strip()]
        print(f"Using {len(accessions)} GEO accessions from --accessions\n")
    elif args.pmids:
        input_type = "pubmed"
        pmids = [p.strip() for p in args.pmids.split(",") if p.strip()]
        print(f"Using {len(pmids)} PMIDs from --pmids\n")
    elif args.dois:
        input_type = "pubmed"
        dois = [d.strip() for d in args.dois.split(",") if d.strip()]
        pmids = _resolve_dois(dois)
    elif args.input_file.endswith(".eml"):
        input_type = "pubmed"
        body = read_eml_body(args.input_file)
        pmids = extract_pmids(body)
        print(f"Found {len(pmids)} PMIDs in {args.input_file}\n")
    else:
        first_value = _peek_first_value(args.input_file)
        if first_value and first_value.upper().startswith(("GSE", "GDS", "GSM", "GPL")):
            input_type = "geo"
            accessions = read_geo_file(args.input_file)
            print(f"Read {len(accessions)} GEO accessions from {args.input_file}\n")
        elif first_value and first_value.startswith("10."):
            input_type = "pubmed"
            dois = [l.strip() for l in open(args.input_file) if l.strip() and not l.startswith("#")]
            pmids = _resolve_dois(dois)
        else:
            input_type = "pubmed"
            pmids = read_pmids_file(args.input_file)
            print(f"Read {len(pmids)} PMIDs from {args.input_file}\n")

    if input_type == "geo":
        if not accessions:
            print("No accessions found. Exiting.")
            sys.exit(1)
        run_geo(
            client=client,
            accessions=accessions,
            criterion=criterion,
            fields_description=fields,
            output_path=args.output,
            max_chars=args.max_chars,
        )
    else:
        if not pmids:
            print("No PMIDs found. Exiting.")
            sys.exit(1)
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


def _resolve_dois(dois: list[str]) -> list[str]:
    """Convert a list of DOIs to PMIDs, skipping any that can't be resolved."""
    pmids = []
    for doi in dois:
        pmid = doi_to_pmid(doi)
        if pmid:
            print(f"  {doi} → PMID {pmid}")
            pmids.append(pmid)
        else:
            print(f"  {doi} → could not resolve (skipped)")
    print(f"Resolved {len(pmids)}/{len(dois)} DOIs to PMIDs\n")
    return pmids


def _peek_first_value(path: str) -> str | None:
    """Return the first non-blank, non-comment line of a file."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return None


if __name__ == "__main__":
    main()


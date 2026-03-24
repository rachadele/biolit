"""CLI entry point for biolit."""
import argparse
import os
import sys
from importlib.metadata import version, PackageNotFoundError

from dotenv import load_dotenv

from biolit.config import load_config
from biolit.fetchers.pubmed import doi_to_pmid
from biolit.llm import get_llm_client
from biolit.pipeline import run, screen_by_pmid, screen_by_geo, screen_by_doi
from biolit.parsers.utils import DEFAULT_MAX_CHARS
from biolit.utils import read_eml_body, extract_pmids, read_pmids_file, read_dois_from_bib

load_dotenv()

DEFAULT_CRITERION = (
    "Is this paper SPECIFICALLY about schizophrenia AND does it use genetics "
    "or genomics methods (e.g. GWAS, WGS, scRNA-seq, proteomics, gene expression)?"
)
DEFAULT_FIELDS = "methodology, sample_type, causal_claims, summary"


def _get_version() -> str:
    try:
        return version("biolit")
    except PackageNotFoundError:
        return "unknown"


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "--version":
        print(_get_version())
        return
    if argv and argv[0] == "screen":
        return _screen_main(argv[1:])
    return _run_main(argv)


def _screen_main(argv: list[str] | None = None) -> None:
    """biolit screen — quickly screen a single paper or GEO record for relevance."""
    import json

    parser = argparse.ArgumentParser(
        prog="biolit screen",
        description="Screen a single paper or GEO record for relevance.",
        epilog=(
            "Examples:\n"
            "  biolit screen --pmid 41627908 --default\n"
            "  biolit screen --accession GSE53987 --default\n"
            "  biolit screen --doi 10.1101/2025.03.17.25324098 --default\n"
            "  biolit screen --pmid 41627908 --criterion 'Is this about GWAS?'\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    id_group = parser.add_mutually_exclusive_group(required=True)
    id_group.add_argument("--pmid", help="PubMed ID to screen")
    id_group.add_argument("--doi", help="DOI to screen (resolved to PMID via NCBI if possible)")
    id_group.add_argument("--accession", help="GEO accession to screen")
    parser.add_argument("--criterion", default=None, help="Relevance question (yes/no)")
    parser.add_argument("--default", action="store_true", help="Use schizophrenia genomics criterion")
    parser.add_argument("--unpaywall-email", default=os.environ.get("UNPAYWALL_EMAIL"),
                        help="Email for Unpaywall API (set UNPAYWALL_EMAIL env var, or pass here)")
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
                                   unpaywall_email=args.unpaywall_email)
        else:
            print(f"Could not resolve {args.doi} to a PMID — fetching directly by DOI")
            result = screen_by_doi(client, args.doi, criterion,
                                   unpaywall_email=args.unpaywall_email)
    elif args.pmid:
        result = screen_by_pmid(client, args.pmid, criterion,
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
            "Accepts a PubMed alert email (.eml), a plain-text file of identifiers, "
            "or inline identifiers via --ids. Identifiers can be PMIDs, DOIs, or GEO "
            "accessions — mixed lists are supported. Optionally screens each record for "
            "relevance (--criterion) and/or extracts structured fields (--fields) into a CSV."
        ),
        epilog=(
            "Examples:\n"
            "  biolit alert.eml --default\n"
            "  biolit identifiers.txt --default\n"
            "  biolit --ids 41795042,GSE53987,10.1101/2025.03.17.25324098 --default\n"
            "  biolit alert.eml --default --unpaywall-email you@example.com\n"
            "  biolit identifiers.txt --criterion 'Is this about treatment-resistant schizophrenia?' "
            "--fields 'methodology, sample_size, outcomes'\n"
            "\n"
            "Environment variables:\n"
            "  ANTHROPIC_API_KEY   Required for Anthropic provider (default)\n"
            "  OPENAI_API_KEY      Required for OpenAI provider\n"
            "  NCBI_API_KEY        Optional; increases NCBI rate limits\n"
            "  UNPAYWALL_EMAIL     Email for Unpaywall API (used during full-text retrieval)\n"
            "  LLM_PROVIDER        Default provider if --provider not set\n"
            "  LLM_MODEL           Default model if --model not set\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_get_version()}")
    parser.add_argument(
        "input_file", nargs="?", default=None,
        help=(
            "PubMed alert .eml file, or a plain-text file of identifiers "
            "(PMIDs, DOIs, GEO accessions — one per line, mixed types accepted)"
        ),
    )
    parser.add_argument(
        "--ids", default=None,
        help=(
            "Comma-separated identifiers: PMIDs, DOIs, GEO accessions, or any mix. "
            "Example: --ids 41795042,GSE53987,10.1101/2025.03.17.25324098"
        ),
    )

    # Screening / extraction
    parser.add_argument("--criterion", default=None,
                        help="Relevance screening criterion (yes/no question). "
                             "Omit to skip screening and process all records.")
    parser.add_argument("--fields", default=None,
                        help="Fields to extract (comma-separated names). "
                             f"Default: \"{DEFAULT_FIELDS}\".")
    parser.add_argument("--default", action="store_true",
                        help="Use default schizophrenia genomics criterion and fields")
    parser.add_argument("--config", default=None, metavar="FILE",
                        help="JSON config file. Keys: criterion, fields, provider, model, "
                             "sections, max_chars, unpaywall_email, output. "
                             "CLI flags take precedence over config values.")
    parser.add_argument("--output", default=None,
                        help="Output CSV path (default: results.csv)")
    parser.add_argument("--markdown", "--md", action="store_true", default=False,
                        help="Also write a results.md markdown summary alongside the CSV")

    # LLM provider
    parser.add_argument(
        "--provider", default=None,
        choices=["anthropic", "openai", "ollama"],
        help="LLM provider (default: anthropic, or LLM_PROVIDER env var, or config file)",
    )
    parser.add_argument(
        "--model", default=None,
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

    # Full-text options
    parser.add_argument(
        "--unpaywall-email", default=None,
        help="Email for Unpaywall API (or set UNPAYWALL_EMAIL env var, or config file)",
    )
    parser.add_argument(
        "--sections", default=None,
        help=(
            "Comma-separated list of sections to send to the LLM when full text is available "
            "(e.g. 'methods,results'). Default: all sections."
        ),
    )
    parser.add_argument(
        "--max-chars", type=int, default=None,
        help=f"Maximum characters of paper text sent to the LLM (default: {DEFAULT_MAX_CHARS})",
    )

    args = parser.parse_args(argv)

    # Load config file (if given) — CLI flags take precedence
    config: dict = {}
    if args.config:
        try:
            config = load_config(args.config)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error loading config: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"Error loading config {args.config}: {e}")
            sys.exit(1)

    # Resolve all settings: CLI flag > config file > env var > hardcoded default
    if args.default:
        criterion = DEFAULT_CRITERION
        fields = DEFAULT_FIELDS
    else:
        criterion = args.criterion or config.get("criterion")
        fields = args.fields or config.get("fields") or DEFAULT_FIELDS

    provider = args.provider or config.get("provider") or os.environ.get("LLM_PROVIDER", "anthropic")
    model = args.model or config.get("model") or os.environ.get("LLM_MODEL")
    unpaywall_email = args.unpaywall_email or config.get("unpaywall_email") or os.environ.get("UNPAYWALL_EMAIL")
    sections_str = args.sections or config.get("sections")
    max_chars = args.max_chars or config.get("max_chars") or DEFAULT_MAX_CHARS
    output = args.output or config.get("output") or "results.csv"
    use_markdown = args.markdown or bool(config.get("markdown"))

    # Build LLM client
    try:
        extra: dict = {}
        if provider == "openai" and args.openai_base_url:
            extra["base_url"] = args.openai_base_url
        if provider == "ollama":
            extra["base_url"] = args.ollama_url
        client = get_llm_client(provider, model, **extra)
    except (EnvironmentError, ImportError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"Using LLM: {client}\n")

    input_file = args.input_file or config.get("input_file")

    if not input_file and not args.ids and not config.get("ids"):
        print("Error: provide an input_file, --ids, or 'ids'/'input_file' in config.")
        sys.exit(1)

    # Build the identifier list from whichever input was given.
    if args.ids:
        ids = [x.strip() for x in args.ids.split(",") if x.strip()]
        print(f"Using {len(ids)} identifiers from --ids\n")
    elif config.get("ids"):
        ids = [x.strip() for x in config["ids"].split(",") if x.strip()]
        print(f"Using {len(ids)} identifiers from config\n")
    elif input_file and input_file.endswith(".eml"):
        body = read_eml_body(input_file)
        ids = extract_pmids(body)
        print(f"Found {len(ids)} PMIDs in {input_file}\n")
    elif input_file and input_file.endswith(".bib"):
        ids = read_dois_from_bib(input_file)
        print(f"Found {len(ids)} DOIs in {input_file}\n")
    else:
        ids = _read_ids_file(input_file)
        print(f"Read {len(ids)} identifiers from {input_file}\n")

    if not ids:
        print("No identifiers found. Exiting.")
        sys.exit(1)

    sections_wanted = (
        [s.strip() for s in sections_str.split(",") if s.strip()]
        if sections_str
        else None
    )
    run(
        client=client,
        ids=ids,
        criterion=criterion,
        fields_description=fields,
        output_path=output,
        unpaywall_email=unpaywall_email,
        sections_wanted=sections_wanted,
        max_chars=max_chars,
        markdown=use_markdown,
    )


def _read_ids_file(path: str) -> list[str]:
    """Read a plain-text file of identifiers (one per line, comments ignored)."""
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


if __name__ == "__main__":
    main()

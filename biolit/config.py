"""Load biolit JSON config files."""
import json

VALID_KEYS = {
    "ids",
    "input_file",
    "criterion",
    "fields",
    "provider",
    "model",
    "sections",
    "max_chars",
    "unpaywall_email",
    "output",
}


def load_config(path: str) -> dict:
    """Load a JSON config file and return the settings dict.

    Recognised keys: ids, input_file, criterion, fields, provider, model,
    sections, max_chars, unpaywall_email, output.

    ``fields`` may be a comma-separated string (field names only) or a JSON
    object mapping field names to extraction descriptions:

        "fields": {
            "tf_name": "HGNC symbol of the transcription factor perturbed",
            "organism": "scientific name of the organism used"
        }

    Raises ValueError for unknown keys, FileNotFoundError / json.JSONDecodeError
    on bad paths or malformed JSON.
    """
    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)
    if not isinstance(config, dict):
        raise ValueError("Config file must be a JSON object")
    unknown = set(config) - VALID_KEYS
    if unknown:
        raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
    if "fields" in config and not isinstance(config["fields"], (str, dict)):
        raise ValueError("'fields' must be a string or object")
    return config

"""Tests for biolit.config.load_config."""
import json
import pytest

from biolit.config import load_config, VALID_KEYS


class TestLoadConfig:
    """Tests for load_config(path)."""

    def test_happy_path_all_keys(self, tmp_path):
        """Valid JSON with all supported keys returns the correct dict."""
        cfg = {
            "criterion": "Is this about schizophrenia?",
            "fields": "methodology, summary",
            "provider": "anthropic",
            "model": "claude-haiku-4-5-20251001",
            "sections": "methods,results",
            "max_chars": 50000,
            "unpaywall_email": "test@example.com",
            "output": "out.csv",
        }
        f = tmp_path / "config.json"
        f.write_text(json.dumps(cfg))
        result = load_config(str(f))
        assert result == cfg

    def test_happy_path_subset_of_keys(self, tmp_path):
        """Valid JSON with only some keys works fine."""
        cfg = {"criterion": "Is this relevant?", "fields": "summary"}
        f = tmp_path / "config.json"
        f.write_text(json.dumps(cfg))
        result = load_config(str(f))
        assert result["criterion"] == "Is this relevant?"
        assert result["fields"] == "summary"
        assert len(result) == 2

    def test_empty_object_is_valid(self, tmp_path):
        """An empty JSON object is valid (no required keys)."""
        f = tmp_path / "config.json"
        f.write_text("{}")
        result = load_config(str(f))
        assert result == {}

    def test_unknown_key_raises_value_error(self, tmp_path):
        """A JSON object with an unknown key raises ValueError listing the key."""
        cfg = {"criterion": "ok", "unknown_key": "bad"}
        f = tmp_path / "config.json"
        f.write_text(json.dumps(cfg))
        with pytest.raises(ValueError) as exc_info:
            load_config(str(f))
        assert "unknown_key" in str(exc_info.value)

    def test_multiple_unknown_keys_all_listed(self, tmp_path):
        """All unknown keys are listed in the ValueError."""
        cfg = {"alpha": 1, "beta": 2}
        f = tmp_path / "config.json"
        f.write_text(json.dumps(cfg))
        with pytest.raises(ValueError) as exc_info:
            load_config(str(f))
        msg = str(exc_info.value)
        assert "alpha" in msg
        assert "beta" in msg

    def test_missing_file_raises_file_not_found_error(self, tmp_path):
        """A path that doesn't exist raises FileNotFoundError."""
        missing = str(tmp_path / "nonexistent.json")
        with pytest.raises(FileNotFoundError):
            load_config(missing)

    def test_malformed_json_raises_json_decode_error(self, tmp_path):
        """A file with malformed JSON raises json.JSONDecodeError."""
        import json as json_module
        f = tmp_path / "bad.json"
        f.write_text("{not valid json}")
        with pytest.raises(json_module.JSONDecodeError):
            load_config(str(f))

    def test_json_array_raises_value_error(self, tmp_path):
        """A JSON array (not an object) raises ValueError."""
        f = tmp_path / "array.json"
        f.write_text('["criterion", "fields"]')
        with pytest.raises(ValueError, match="JSON object"):
            load_config(str(f))

    def test_json_null_raises_value_error(self, tmp_path):
        """A JSON null value (not an object) raises ValueError."""
        f = tmp_path / "null.json"
        f.write_text("null")
        with pytest.raises(ValueError, match="JSON object"):
            load_config(str(f))

    def test_valid_keys_set_matches_documented_keys(self):
        """VALID_KEYS contains exactly the documented supported keys."""
        expected = {
            "ids", "input_file", "criterion", "fields", "provider", "model",
            "sections", "max_chars", "unpaywall_email", "output",
        }
        assert VALID_KEYS == expected

    def test_input_file_key_is_accepted(self, tmp_path):
        """input_file is a valid config key and its value is returned as-is."""
        cfg = {"input_file": "docs/alert.eml"}
        f = tmp_path / "config.json"
        f.write_text(json.dumps(cfg))
        result = load_config(str(f))
        assert result["input_file"] == "docs/alert.eml"

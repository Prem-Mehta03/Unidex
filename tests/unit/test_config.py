from pathlib import Path

import pytest

from unidex.config import Settings
from unidex.exceptions import ConfigError


def test_defaults_when_nothing_is_set() -> None:
    settings = Settings.from_mapping({})
    assert settings.db_path == Path("data/unidex.db")
    assert settings.log_level == "INFO"


def test_values_are_read_and_normalised() -> None:
    settings = Settings.from_mapping({"UNIDEX_DB_PATH": "x/y.db", "UNIDEX_LOG_LEVEL": " debug "})
    assert settings.db_path == Path("x/y.db")
    assert settings.log_level == "DEBUG"


def test_invalid_log_level_raises() -> None:
    with pytest.raises(ConfigError):
        Settings.from_mapping({"UNIDEX_LOG_LEVEL": "LOUD"})


def test_empty_db_path_raises() -> None:
    with pytest.raises(ConfigError):
        Settings.from_mapping({"UNIDEX_DB_PATH": "  "})


def test_llm_settings_default_to_free_tier_limits() -> None:
    settings = Settings.from_mapping({})
    assert settings.gemini_api_key is None
    assert settings.gemini_model is None
    assert (settings.llm_requests_per_minute, settings.llm_requests_per_day) == (5, 20)


def test_llm_settings_are_read() -> None:
    settings = Settings.from_mapping(
        {
            "GEMINI_API_KEY": " abc ",
            "GEMINI_MODEL": "gemini-x",
            "LLM_REQUESTS_PER_MINUTE": "3",
            "LLM_REQUESTS_PER_DAY": "10",
        }
    )
    assert (settings.gemini_api_key, settings.gemini_model) == ("abc", "gemini-x")
    assert (settings.llm_requests_per_minute, settings.llm_requests_per_day) == (3, 10)


def test_blank_key_counts_as_not_set() -> None:
    assert Settings.from_mapping({"GEMINI_API_KEY": "   "}).gemini_api_key is None


@pytest.mark.parametrize("value", ["0", "-2", "many", "2.5"])
def test_bad_limits_raise(value: str) -> None:
    with pytest.raises(ConfigError):
        Settings.from_mapping({"LLM_REQUESTS_PER_DAY": value})


def test_api_key_never_appears_in_repr() -> None:
    settings = Settings.from_mapping({"GEMINI_API_KEY": "super-secret"})
    assert "super-secret" not in repr(settings)


def test_render_url_is_used_unless_overridden() -> None:
    assert Settings.from_mapping({"RENDER_EXTERNAL_URL": "https://u.onrender.com/"}).public_url == (
        "https://u.onrender.com"
    )
    both = {"RENDER_EXTERNAL_URL": "https://u.onrender.com", "UNIDEX_PUBLIC_URL": "https://my.site"}
    assert Settings.from_mapping(both).public_url == "https://my.site"
    assert Settings.from_mapping({}).public_url is None

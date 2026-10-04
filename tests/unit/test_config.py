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

"""Application settings, read from environment variables in one place.

Nothing else in the code base reads ``os.environ`` directly. That keeps secrets
out of the source, and makes it easy to see every knob the app has.

Settings are added stage by stage; ``.env.example`` already lists the variables
that later stages (LLM, Google login) will use.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from unidex.exceptions import ConfigError

DEFAULT_DB_PATH = "data/unidex.db"
DEFAULT_LOG_LEVEL = "INFO"
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable application settings.

    Attributes:
        db_path: Location of the SQLite database file.
        log_level: Logging level name (see ``VALID_LOG_LEVELS``).
    """

    db_path: Path
    log_level: str

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> "Settings":
        """Build settings from a mapping of variable names to values.

        Taking a plain mapping (instead of reading ``os.environ``) makes this
        easy to unit-test.

        Args:
            env: Variable names and values, e.g. ``os.environ`` or a test dict.

        Returns:
            A validated :class:`Settings` instance.

        Raises:
            ConfigError: If a value is invalid.
        """
        log_level = env.get("UNIDEX_LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
        if log_level not in VALID_LOG_LEVELS:
            valid = ", ".join(sorted(VALID_LOG_LEVELS))
            raise ConfigError(f"UNIDEX_LOG_LEVEL must be one of: {valid} (got {log_level!r})")

        db_path = env.get("UNIDEX_DB_PATH", DEFAULT_DB_PATH).strip()
        if not db_path:
            raise ConfigError("UNIDEX_DB_PATH must not be empty")

        return cls(db_path=Path(db_path), log_level=log_level)


def load_settings(env_file: Path | None = None) -> Settings:
    """Load settings from a ``.env`` file (if present) and the environment.

    Real environment variables win over values in the ``.env`` file.

    Args:
        env_file: Optional explicit path to a ``.env`` file. When omitted,
            ``python-dotenv`` looks for ``.env`` in the current directory
            and its parents.

    Returns:
        A validated :class:`Settings` instance.

    Raises:
        ConfigError: If a value is invalid.
    """
    load_dotenv(dotenv_path=env_file, override=False)
    return Settings.from_mapping(os.environ)

"""Application settings, read from environment variables in one place.

Nothing else in the code base reads ``os.environ`` directly. That keeps secrets
out of the source, and makes it easy to see every knob the app has.

Settings are added stage by stage; ``.env.example`` already lists the variables
that later stages (LLM, Google login) will use.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from unidex.exceptions import ConfigError

DEFAULT_DB_PATH = "data/unidex.db"
DEFAULT_LOG_LEVEL = "INFO"
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
DEFAULT_REQUESTS_PER_MINUTE = 5
DEFAULT_REQUESTS_PER_DAY = 20


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    """Read a whole number of at least 1 from the environment mapping.

    Args:
        env: Variable names and values.
        name: Variable to read.
        default: Value used when the variable is absent or blank.

    Returns:
        The parsed number.

    Raises:
        ConfigError: If the value is not a whole number of at least 1.
    """
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number (got {raw!r})") from exc
    if value < 1:
        raise ConfigError(f"{name} must be at least 1 (got {value})")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable application settings.

    Attributes:
        db_path: Location of the SQLite database file.
        log_level: Logging level name (see ``VALID_LOG_LEVELS``).
        gemini_api_key: Gemini API key, or ``None`` when not configured. Hidden
            from ``repr`` so it cannot leak into logs or error messages.
        gemini_model: Model id copied from AI Studio, or ``None``.
        llm_requests_per_minute: Free-tier requests-per-minute limit.
        llm_requests_per_day: Free-tier requests-per-day limit.
    """

    db_path: Path
    log_level: str
    gemini_api_key: str | None = field(default=None, repr=False)
    gemini_model: str | None = None
    llm_requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE
    llm_requests_per_day: int = DEFAULT_REQUESTS_PER_DAY

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

        return cls(
            db_path=Path(db_path),
            log_level=log_level,
            gemini_api_key=env.get("GEMINI_API_KEY", "").strip() or None,
            gemini_model=env.get("GEMINI_MODEL", "").strip() or None,
            llm_requests_per_minute=_positive_int(
                env, "LLM_REQUESTS_PER_MINUTE", DEFAULT_REQUESTS_PER_MINUTE
            ),
            llm_requests_per_day=_positive_int(
                env, "LLM_REQUESTS_PER_DAY", DEFAULT_REQUESTS_PER_DAY
            ),
        )


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

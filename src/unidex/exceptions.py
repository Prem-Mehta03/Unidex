"""Custom exception types used across Unidex.

Every error the project raises on purpose derives from :class:`UnidexError`, so
callers can catch "anything Unidex-specific" without also swallowing unrelated
bugs such as ``KeyError`` or ``TypeError``.
"""


class UnidexError(Exception):
    """Base class for all errors raised deliberately by Unidex."""


class ConfigError(UnidexError):
    """Raised when configuration (environment variables) is missing or invalid."""


class IngestionError(UnidexError):
    """Raised when a file listing or a seed file cannot be read or is malformed."""


class DatabaseError(UnidexError):
    """Raised when the database is in a state Unidex cannot safely continue from."""


class ExtractionError(UnidexError):
    """Raised when metadata extraction cannot proceed (for example a bad review file)."""


class LLMError(UnidexError):
    """Raised when a language-model request fails or returns something unusable."""


class LLMRateLimitError(LLMError):
    """Raised when the provider rejects a request because a quota was reached."""


class LLMBudgetExceededError(LLMError):
    """Raised before a request is sent if it would exceed the configured daily budget."""


class AuthError(UnidexError):
    """Raised when a sign-in attempt fails or is not allowed."""

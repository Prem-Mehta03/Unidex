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

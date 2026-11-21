"""
Custom exceptions and internal control signals for Exa.
"""


class ExaError(Exception):
    """Base exception for all Exa errors."""


class LexerError(ExaError):
    """Raised when the lexer encounters invalid characters or unterminated literals."""


class ParserError(ExaError):
    """Raised when the parser encounters an unexpected token or syntax error."""


class ExaRuntimeError(ExaError):
    """Raised during execution (catchable by the 'over' fallback operator)."""


class PermissionDeniedError(ExaRuntimeError):
    """Raised when binary chmod permissions or HYMN_CODE rank deny execution."""


class MethodSuspendedError(ExaRuntimeError):
    """Raised when attempting to execute a method currently in SUSPEND state."""


class SkipItem(Exception):
    """Internal control signal used by FILTER to drop an item from a stream."""
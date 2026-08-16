"""GxError: the single exception type that crosses the CLI/MCP boundary.

Every subcommand's error path resolves to one of these `kind`s, each with a
fixed process exit code (design-contract.md section 2):
    usage=2  environment=3  render=4  audio=5  not_converged=6  refused=7
Anything else (programming errors, unexpected exceptions) surfaces as
kind="internal", exit_code=1.
"""
from __future__ import annotations

SCHEMA_ERROR = "gx-mimic/error/1"

KIND_EXIT_CODES = {
    "usage": 2,
    "environment": 3,
    "render": 4,
    "audio": 5,
    "not_converged": 6,
    "refused": 7,
    "internal": 1,
}


class GxError(Exception):
    """Structured, JSON-serializable error.

    `kind` must be one of KIND_EXIT_CODES' keys. `exit_code` is derived from
    `kind` unless explicitly overridden (not_converged in particular still
    carries a `best` result alongside the error at the call site -- that's
    handled by the caller, not by this class).
    """

    def __init__(self, kind: str, message: str, hint: str | None = None, exit_code: int | None = None):
        if kind not in KIND_EXIT_CODES:
            raise ValueError(f"unknown GxError kind: {kind!r}")
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.hint = hint
        self.exit_code = exit_code if exit_code is not None else KIND_EXIT_CODES[kind]

    def to_json(self) -> dict:
        return {
            "schema": SCHEMA_ERROR,
            "kind": self.kind,
            "message": self.message,
            "hint": self.hint,
            "exit_code": self.exit_code,
        }

    def __str__(self) -> str:
        return f"[{self.kind}] {self.message}"

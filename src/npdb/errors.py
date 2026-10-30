"""Error taxonomy shared by the library and the CLI."""

from __future__ import annotations

import os
import re

ISSUES_URL = "https://github.com/neuropoly/neuropoly-db/issues"
WIKI_URL = "https://github.com/neuropoly/neuropoly-db/wiki"


class NpdbError(Exception):
    """An anticipated failure the user can act on. Rendered without a traceback."""

    title = "Error"
    exit_code = 1

    def __init__(
        self, message: str, *, hint: str | None = None, docs: str | None = None
    ):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.docs = docs


class UsageError(NpdbError, ValueError):
    title = "Invalid usage"
    exit_code = 2


class ConfigurationError(NpdbError, ValueError):
    title = "Configuration error"


class MissingDependencyError(NpdbError):
    title = "Missing dependency"


class GitCommandError(NpdbError, RuntimeError):
    title = "Git error"


_SECRET_ENV = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|CREDENTIALS)", re.IGNORECASE)
_SECRET_PATTERNS = [
    (
        re.compile(r"(Authorization:\s*(?:Basic|Bearer|token)\s+)\S+", re.IGNORECASE),
        r"\1***",
    ),
    (
        re.compile(
            r"([?&](?:access_token|token|api_key|key)=)[^&\s'\"]+", re.IGNORECASE
        ),
        r"\1***",
    ),
    (re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1***@"),
]


def redact(text: str) -> str:
    """Remove credentials and personal paths from text shown to users or pasted in issues."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    for name, value in os.environ.items():
        if len(value) >= 6 and _SECRET_ENV.search(name):
            text = text.replace(value, "***")

    home = os.path.expanduser("~")
    if home and home != "/":
        text = text.replace(home, "~")
    return text

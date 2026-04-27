"""
Bagel CLI error types and error classifier.

Defines BagelCLIError (structured exception) and classify_bagel_error()
which maps known Bagel error patterns to actionable user guidance.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

from rich.text import Text


@dataclass
class BagelCLIError(RuntimeError):
    """
    Raised when the Bagel CLI exits with a non-zero exit code.

    Attributes:
        command: The CLI args that were invoked (space-joined).
        exit_code: Non-zero exit code from the CLI runner.
        plain_output: Raw output string (may contain ANSI escape codes).
        rich_output: Output rendered as a Rich Text object (ANSI decoded).
    """

    command: str
    exit_code: int
    plain_output: str
    rich_output: Text = field(default_factory=Text)

    def __post_init__(self):
        super().__init__(
            f"Bagel CLI failed (exit {self.exit_code}): {self.command}"
        )

    @classmethod
    def from_result(cls, command: str, exit_code: int, output: str) -> "BagelCLIError":
        return cls(
            command=command,
            exit_code=exit_code,
            plain_output=output,
            rich_output=Text.from_ansi(output),
        )


# ---------------------------------------------------------------------------
# Error pattern registry
# Each entry: (compiled_regex, problem_name, description, fix_steps)
# ---------------------------------------------------------------------------

_PATTERN_REGISTRY: List[
    tuple[re.Pattern, str, str, List[str]]
] = [
    (
        re.compile(r"missing from the phenotypic table", re.IGNORECASE),
        "Missing annotated columns",
        "The annotations dictionary references columns that are absent from phenotypes.tsv.",
        [
            "Open phenotypes_annotations.json and check every top-level key.",
            "Verify the same column names exist as headers in phenotypes.tsv.",
            "Remove or rename entries that do not match.",
        ],
    ),
    (
        re.compile(r"duplicate participant IDs?", re.IGNORECASE),
        "Duplicate participant IDs",
        "phenotypes.tsv contains repeated participant_id values.",
        [
            "Run: awk -F'\\t' 'NR>1{print $1}' phenotypes.tsv | sort | uniq -d",
            "Remove or merge duplicate rows before re-running.",
        ],
    ),
    (
        re.compile(r"unsupported vocabulary namespace prefix", re.IGNORECASE),
        "Unsupported namespace prefix",
        "A term URL in phenotypes_annotations.json uses an unrecognised namespace.",
        [
            "Check all 'termURL' values in phenotypes_annotations.json.",
            "Allowed prefixes include: nb, snomed, ncit, ilx, nidm.",
            "Replace or remove unsupported prefixes.",
        ],
    ),
    (
        re.compile(
            r"must contain at least one column annotated as being about participant ID",
            re.IGNORECASE,
        ),
        "Missing participant ID annotation",
        "No column in phenotypes_annotations.json is annotated as the participant identifier.",
        [
            "Ensure 'participant_id' (or equivalent) has isAbout.label = 'Participant ID'.",
            "Re-run annotation with a corrected dictionary.",
        ],
    ),
    (
        re.compile(
            r"missing values in participant or session ID columns?", re.IGNORECASE
        ),
        "Empty ID rows",
        "One or more rows in phenotypes.tsv have a blank participant_id or session_id.",
        [
            "Inspect phenotypes.tsv for rows with empty first or second column.",
            "Fill or remove empty ID values before re-running.",
        ],
    ),
    (
        re.compile(r"not valid JSON|Failed to decode", re.IGNORECASE),
        "Malformed input file",
        "phenotypes_annotations.json (or another JSON input) could not be parsed.",
        [
            "Validate the file with: python -m json.tool phenotypes_annotations.json",
            "Fix any syntax errors (trailing commas, unquoted keys, etc.).",
        ],
    ),
]


def classify_bagel_error(plain_text: str) -> List[dict]:
    """
    Match ``plain_text`` against known Bagel error patterns.

    Returns a list of dicts with keys:
      - ``problem``: short name
      - ``description``: human-readable description
      - ``fix_steps``: ordered list of resolution steps

    Returns an empty list if no pattern matches (caller shows generic fallback).
    """
    results = []
    for pattern, name, description, fix_steps in _PATTERN_REGISTRY:
        if pattern.search(plain_text):
            results.append(
                {
                    "problem": name,
                    "description": description,
                    "fix_steps": fix_steps,
                }
            )
    return results

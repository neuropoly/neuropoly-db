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
# Step helper — create a resolution step dict
# ---------------------------------------------------------------------------

def _step(action: str, detail: str, auto_fixable: bool = False) -> dict:
    """Return a resolution-step dict with action, detail, and auto_fixable flag."""
    return {"action": action, "detail": detail, "auto_fixable": auto_fixable}


# ---------------------------------------------------------------------------
# Error pattern registry
# Each entry: (compiled_regex, problem_name, description, fix_steps)
# fix_steps is now a list of dicts: {action, detail, auto_fixable}
# ---------------------------------------------------------------------------

_PATTERN_REGISTRY: List[
    tuple[re.Pattern, str, str, List[dict]]
] = [
    (
        re.compile(r"missing from the phenotypic table", re.IGNORECASE),
        "Missing annotated columns",
        "The annotations dictionary references columns that are absent from phenotypes.tsv.",
        [
            _step(
                "Audit annotation keys against TSV headers",
                "Open phenotypes_annotations.json and compare its top-level keys with the "
                "column headers in phenotypes.tsv — every key must have a matching column. "
                "The Bagel error message names the missing column(s) explicitly.",
            ),
            _step(
                "Remove or rename orphan annotation keys",
                "Delete entries whose column no longer exists in the TSV, or rename them to "
                "match the current header spelling. Bagel treats any annotated key absent "
                "from the TSV as a fatal mismatch.",
                auto_fixable=True,
            ),
        ],
    ),
    (
        re.compile(r"duplicate participant IDs?", re.IGNORECASE),
        "Duplicate participant IDs",
        "phenotypes.tsv contains repeated participant_id values.",
        [
            _step(
                "Identify duplicated IDs",
                "Run: awk -F'\\t' 'NR>1{print $1}' phenotypes.tsv | sort | uniq -d\n"
                "This prints every participant_id that appears more than once.",
            ),
            _step(
                "Deduplicate rows (keep first occurrence)",
                "Bagel requires one row per participant_id. Duplicate rows are removed "
                "automatically by keeping only the first occurrence — later rows are dropped.",
                auto_fixable=True,
            ),
        ],
    ),
    (
        re.compile(r"unsupported vocabulary namespace prefix", re.IGNORECASE),
        "Unsupported namespace prefix",
        "A term URL in phenotypes_annotations.json uses an unrecognised namespace.",
        [
            _step(
                "Inspect all termURL values",
                "Search phenotypes_annotations.json for every 'termURL' field. "
                "Allowed prefixes are: nb, snomed, ncit, ilx, nidm. "
                "The Bagel error will quote the offending prefix.",
            ),
            _step(
                "Replace or remove unsupported prefixes",
                "Map the unsupported namespace to the nearest allowed one, or remove "
                "the termURL field if no mapping exists. Re-run annotation after fixing.",
            ),
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
            _step(
                "Annotate participant_id column with isAbout = nb:ParticipantID",
                "In phenotypes_annotations.json find the participant identifier column "
                "(usually 'participant_id') and set its Annotations.IsAbout.TermURL to "
                "'nb:ParticipantID' and IsAbout.Label to 'Participant ID'.",
            ),
            _step(
                "Re-run annotation",
                "Re-run with a corrected dictionary. Bagel requires exactly one column "
                "annotated as the participant identifier before it can build the graph.",
            ),
        ],
    ),
    (
        re.compile(
            r"missing values in participant or session ID columns?", re.IGNORECASE
        ),
        "Empty ID rows",
        "One or more rows in phenotypes.tsv have a blank participant_id or session_id.",
        [
            _step(
                "Find rows with empty participant or session IDs",
                "Run: awk -F'\\t' 'NR>1 && ($1==\"\" || $2==\"\")' phenotypes.tsv\n"
                "Blank IDs in the first or second column cause Bagel to abort.",
            ),
            _step(
                "Fill or remove empty ID rows",
                "Either assign correct IDs to blank rows or remove them entirely. "
                "Every data row must have a non-empty participant_id (and session_id "
                "if the dataset is session-based).",
                auto_fixable=True,
            ),
        ],
    ),
    (
        re.compile(r"not valid JSON|Failed to decode", re.IGNORECASE),
        "Malformed input file",
        "phenotypes_annotations.json (or another JSON input) could not be parsed.",
        [
            _step(
                "Validate JSON syntax",
                "Run: uv run python -m json.tool phenotypes_annotations.json\n"
                "Common problems: trailing commas after the last element, unquoted keys, "
                "mismatched braces/brackets, or invalid escape sequences.",
            ),
            _step(
                "Fix syntax errors and re-run",
                "Correct all reported JSON errors. A linter such as 'jq .' can help "
                "locate the exact line/column of the first error.",
            ),
        ],
    ),
    (
        re.compile(
            r"No image files with supported BIDS suffixes were found",
            re.IGNORECASE,
        ),
        "Unsupported imaging modality",
        "No BIDS-supported image files were found in this dataset.",
        [
            _step(
                "Annotate phenotypes only (skip imaging steps)",
                "Re-run gitea2bagel without the imaging pipeline. Only bagel pheno will "
                "be called; bids2tsv and bagel bids are skipped for datasets whose modality "
                "is not yet supported by Neurobagel.",
            ),
            _step(
                "Register the modality with --extend-modalities",
                "Pass --extend-modalities to gitea2bagel. npdb will query the LLM (if "
                "configured) or use a built-in heuristic to map the unsupported BIDS suffix "
                "to the best matching Neurobagel Image IRI, register it locally, and retry "
                "the conversion automatically.",
                auto_fixable=True,
            ),
            _step(
                "Open an upstream issue",
                "Open an issue on https://github.com/neurobagel/bagel-cli/issues to "
                "request native support for this imaging modality in future Bagel releases.",
            ),
        ],
    ),
    (
        re.compile(
            r"subject IDs? not found in the provided JSON-LD file[:\s]*(?P<subjects>[^\n]*)",
            re.IGNORECASE,
        ),
        "New subjects not in JSON-LD",
        "Subject IDs in the BIDS dataset are absent from the existing JSON-LD.",
        [
            _step(
                "Regenerate the JSON-LD with bagel pheno",
                "Run 'bagel pheno ...' first so the JSON-LD includes all current subjects. "
                "The missing subject IDs are captured in the 'context.subjects' field of this "
                "ledger entry.",
            ),
            _step(
                "Re-run bagel bids after pheno",
                "Once the JSON-LD is up to date, re-run bagel bids. Subject alignment is "
                "also applied automatically by npdb before the bids step.",
                auto_fixable=True,
            ),
        ],
    ),
    (
        re.compile(
            r"unique values found in annotated categorical columns.*missing annotations",
            re.IGNORECASE | re.DOTALL,
        ),
        "Missing categorical value annotations",
        "Some categorical column values are missing annotation entries.",
        [
            _step(
                "Add missing Levels entries",
                "Open phenotypes_annotations.json and add a Levels entry for every "
                "categorical value that Bagel reports as unannotated. Each entry needs at "
                "minimum a TermURL and a Label.",
            ),
            _step(
                "Inject NA-like values as MissingValues sentinels",
                "Values such as '-', 'n/a', 'NA', 'unknown' are added to the "
                "MissingValues list automatically, preventing them from being treated as "
                "valid categorical levels that require annotation.",
                auto_fixable=True,
            ),
        ],
    ),
    (
        re.compile(
            r"not a valid Neurobagel data dictionary",
            re.IGNORECASE,
        ),
        "Invalid data dictionary schema",
        "The phenotypes annotations JSON does not conform to the Neurobagel data dictionary schema.",
        [
            _step(
                "Validate against the Neurobagel schema",
                "Compare phenotypes_annotations.json against the schema at "
                "https://neurobagel.org/user_guide/annotation_tool/. Every column entry "
                "must have at minimum an Annotations block with IsAbout.TermURL.",
            ),
            _step(
                "Re-run annotation to regenerate the dictionary",
                "The safest fix is to delete the annotations file and re-run the annotation "
                "step so that a schema-compliant file is produced from scratch.",
            ),
        ],
    ),
    (
        re.compile(
            r"File '(?P<path>[^']+)' does not exist",
            re.IGNORECASE,
        ),
        "Missing phenotypes file",
        "A required input file was not found.",
        [
            _step(
                "Verify input file paths",
                "Check that the phenotypes TSV and annotations JSON paths are correct and "
                "that the files are present on disk. The missing path is captured in the "
                "'context.path' field of this ledger entry.",
            ),
            _step(
                "Re-run annotation to regenerate missing files",
                "If the annotations file was accidentally deleted, re-run the annotation "
                "step to produce a fresh copy.",
            ),
        ],
    ),
    (
        re.compile(
            r"could not convert string to float[:\s]+['\"]?(?P<value>[^\s'\"]+)['\"]?",
            re.IGNORECASE,
        ),
        "Invalid age format",
        "An age value could not be parsed as a number.",
        [
            _step(
                "Inspect the age column for non-numeric values",
                "The offending value is captured in the 'context.value' field of this "
                "ledger entry. Common culprits: '-', 'n/a', range strings like '20-30', "
                "bounded values like '89+', or ISO-8601 durations like 'P25Y'.",
            ),
            _step(
                "Auto-correct the age Format.TermURL",
                "fix_age_format() scans all age values in phenotypes.tsv, detects the "
                "actual encoding (nb:FromFloat, nb:FromInt, nb:FromRange, nb:FromBounded, "
                "nb:FromISO8601, nb:FromEuro) and updates the Format.TermURL in "
                "phenotypes_annotations.json in place — no manual editing needed.",
                auto_fixable=True,
            ),
        ],
    ),
    (
        re.compile(r"only one column was found", re.IGNORECASE),
        "Single-column TSV",
        "phenotypes.tsv appears to have only one column; it may use commas instead of tabs.",
        [
            _step(
                "Check the file delimiter",
                "Open phenotypes.tsv in a plain text editor or run: "
                "head -1 phenotypes.tsv | cat -A\n"
                "Tab-separated files show ^I between fields; commas or semicolons indicate "
                "a wrong delimiter.",
            ),
            _step(
                "Convert delimiter to tab automatically",
                "fix_single_column_tsv() detects the dominant non-tab delimiter (comma or "
                "semicolon) and rewrites the file in place using tabs — no manual editing "
                "needed.",
                auto_fixable=True,
            ),
        ],
    ),
]


def classify_bagel_error(plain_text: str) -> List[dict]:
    """
    Match ``plain_text`` against known Bagel error patterns.

    Returns a list of dicts with keys:
      - ``problem``: short name
      - ``description``: human-readable description
      - ``fix_steps``: ordered list of resolution step dicts, each with:
        - ``action``: concise one-line instruction
        - ``detail``: longer explanation with context
        - ``auto_fixable``: bool — whether npdb can apply this fix automatically
      - ``context``: dict of named capture groups extracted from the match
        (e.g. ``{"path": "/some/file.tsv"}``); empty dict when no groups captured

    Returns an empty list if no pattern matches (caller shows generic fallback).
    """
    results = []
    for pattern, name, description, fix_steps in _PATTERN_REGISTRY:
        m = pattern.search(plain_text)
        if m:
            context = {k: v for k, v in m.groupdict().items() if v is not None}
            results.append(
                {
                    "problem": name,
                    "description": description,
                    "fix_steps": fix_steps,
                    "context": context,
                }
            )

    # bids2tsv crash: BIDS validation started but did not complete successfully.
    # Detected by the presence of the BIDS directory banner without the
    # "BIDS validation passed" confirmation line.
    if (
        re.search(r"Input BIDS directory:", plain_text, re.IGNORECASE)
        and not re.search(r"BIDS validation passed", plain_text, re.IGNORECASE)
    ):
        results.append(
            {
                "problem": "bids2tsv crash",
                "description": (
                    "The bids2tsv step started BIDS validation but did not complete "
                    "successfully. Review the captured traceback for the root cause."
                ),
                "fix_steps": [
                    _step(
                        "Review the captured traceback",
                        "The full Python traceback from the bids2tsv crash is included "
                        "in the plain_output field of this ledger entry and in the "
                        "terminal output above. Identify the exact failure point in the "
                        "BIDS validation or conversion pipeline.",
                        auto_fixable=False,
                    ),
                    _step(
                        "Validate the BIDS dataset",
                        "Run: bids-validator <bids_dir>\n"
                        "Fix all reported errors before re-running gitea2bagel. "
                        "Common issues: missing required files, invalid file naming, "
                        "or malformed JSON sidecars.",
                        auto_fixable=False,
                    ),
                ],
                "context": {},
            }
        )

    return results

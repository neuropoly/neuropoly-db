from pathlib import Path

from bids_repair.dataset import compare_participant_ids, scan_imaging_suffixes
from npdb.external.neurobagel.schema import BAGEL_SUPPORTED_SUFFIXES


class PreflightError(RuntimeError):
    """Raised when a pre-flight check fails before any Bagel CLI call."""

    def __init__(
        self,
        problem_name: str,
        description: str,
        fix_steps: list[dict] | None = None,
        raw_snippet: str = "",
    ) -> None:
        super().__init__(description)
        self.problem_name = problem_name
        self.description = description
        self.fix_steps: list[dict] = fix_steps or []
        self.raw_snippet = raw_snippet


def check_bids_suffixes(
    bids_dir: str,
    extra_suffix_map: dict[str, str] | None = None,
) -> tuple[set[str], set[str]]:
    """
    Scan BIDS directory for NIfTI imaging files and classify their suffixes.

    *extra_suffix_map* is an optional ``{suffix: iri}`` dict that extends the
    built-in :data:`BAGEL_SUPPORTED_SUFFIXES`.  Suffixes present in this map
    are treated as supported and will not trigger a :class:`PreflightError`.

    Returns ``(supported_found, unsupported_found)``.  Raises
    :class:`PreflightError` if imaging files are present but none use a
    Bagel-supported BIDS suffix (including extra entries).
    """
    effective_supported = BAGEL_SUPPORTED_SUFFIXES | frozenset(extra_suffix_map or {})
    suffixes = scan_imaging_suffixes(bids_dir)
    supported = suffixes & effective_supported
    unsupported = suffixes - effective_supported

    if not supported and unsupported:
        raise PreflightError(
            problem_name="preflight_failure",
            description=(
                "No supported BIDS imaging files found. "
                f"Dataset only contains unsupported suffixes: {sorted(unsupported)}"
            ),
            fix_steps=[
                {
                    "action": "Annotate phenotypes only (skip imaging steps)",
                    "detail": "Re-run gitea2bagel without the imaging pipeline. "
                    "Only bagel pheno will be called for this dataset.",
                    "auto_fixable": False,
                },
                {
                    "action": "Register the modality with --extend-modalities",
                    "detail": (
                        f"Unsupported suffixes: {sorted(unsupported)}. "
                        "Pass --extend-modalities to gitea2bagel so npdb can map each "
                        "suffix to a Neurobagel Image IRI (via LLM or built-in heuristic) "
                        "and retry the conversion automatically."
                    ),
                    "auto_fixable": True,
                },
                {
                    "action": "Open an upstream issue",
                    "detail": "Open an issue on https://github.com/neurobagel/bagel-cli/issues "
                    "to request native support for this imaging modality.",
                    "auto_fixable": False,
                },
            ],
        )

    return supported, unsupported


def check_missing_files(*paths: str) -> list[str]:
    """Return a list of paths from *paths* that do not exist on disk."""
    return [p for p in paths if not Path(p).exists()]

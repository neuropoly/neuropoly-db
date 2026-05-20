from pathlib import Path

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
    bids_path = Path(bids_dir)
    supported: set[str] = set()
    unsupported: set[str] = set()

    for nii in bids_path.glob("sub-*/**/*.nii*"):
        stem = nii.name
        # Strip .nii.gz or .nii
        if stem.endswith(".nii.gz"):
            stem = stem[:-7]
        elif stem.endswith(".nii"):
            stem = stem[:-4]
        # BIDS suffix is the entity after the last underscore
        suffix = stem.rsplit("_", 1)[-1] if "_" in stem else stem
        if suffix in effective_supported:
            supported.add(suffix)
        else:
            unsupported.add(suffix)

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


def compare_participant_ids(
    pheno_tsv: str, bids_tsv: str
) -> tuple[list[str], list[str]]:
    """
    Compare participant IDs in phenotypes TSV vs BIDS TSV (case-insensitive).

    Returns ``(in_bids_not_pheno, in_pheno_not_bids)``.
    """
    import csv as _csv

    def _read_ids(path: str) -> set[str]:
        ids: set[str] = set()
        try:
            with open(path, "r", encoding="utf-8", newline="") as fh:
                reader = _csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    pid = (row.get("participant_id") or "").strip().lower()
                    if pid:
                        ids.add(pid)
        except OSError:
            pass
        return ids

    pheno_ids = _read_ids(pheno_tsv)
    bids_ids = _read_ids(bids_tsv)

    in_bids_not_pheno = sorted(bids_ids - pheno_ids)
    in_pheno_not_bids = sorted(pheno_ids - bids_ids)
    return in_bids_not_pheno, in_pheno_not_bids


def check_missing_files(*paths: str) -> list[str]:
    """Return a list of paths from *paths* that do not exist on disk."""
    return [p for p in paths if not Path(p).exists()]

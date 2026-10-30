import csv
import re
from pathlib import Path


def scan_imaging_suffixes(dataset: str | Path) -> set[str]:
    suffixes = set()
    for image in Path(dataset).glob("sub-*/**/*.nii*"):
        name = image.name
        if name.endswith(".nii.gz"):
            name = name[:-7]
        elif name.endswith(".nii"):
            name = name[:-4]
        suffixes.add(name.rsplit("_", 1)[-1])

    return suffixes


def compare_participant_ids(
    pheno_tsv: str, bids_tsv: str
) -> tuple[list[str], list[str]]:
    def read_ids(path: str) -> set[str]:
        with open(path, newline="", encoding="utf-8") as stream:
            return {
                row["participant_id"].strip().lower()
                for row in csv.DictReader(stream, delimiter="\t")
                if row.get("participant_id", "").strip()
            }

    phenotype_ids, bids_ids = read_ids(pheno_tsv), read_ids(bids_tsv)
    return sorted(bids_ids - phenotype_ids), sorted(phenotype_ids - bids_ids)


def find_dataset_root(root: Path) -> Path:
    """Return the BIDS root inside ``root``, following wrapper folders.

    Archives often wrap the dataset in a folder such as ``<repo>-<commit>/``.
    The top-level folder wins whenever it already looks like a dataset.
    """
    root = Path(root)
    if (root / "dataset_description.json").is_file() or any(root.glob("sub-*")):
        return root

    excluded = {"__MACOSX", "derivatives", "sourcedata", "stimuli", "code", "phenotype"}
    candidates = [
        path
        for path in [root, *sorted(root.rglob("*"))]
        if path.is_dir()
        and not path.is_symlink()
        and not any(
            part.startswith(".") or part in excluded
            for part in path.relative_to(root).parts
        )
        and (
            (path / "participants.tsv").is_file()
            or (path / "rawdata").is_dir()
            or any(child.is_dir() for child in path.glob("sub-*"))
        )
    ]
    candidates = [
        path
        for path in candidates
        if not any(parent in candidates for parent in path.parents)
    ]
    if len(candidates) > 1:
        found = ", ".join(str(path.relative_to(root)) for path in candidates)
        raise ValueError(f"Multiple dataset roots found in {root}: {found}.")

    return candidates[0] if candidates else root


def prepare_dataset(root: Path) -> Path:
    rawdata = root / "rawdata"
    derivatives = root / "derivatives"
    auxiliary_directories = {"sourcedata", "stimuli", "code", "phenotype"}
    protected_directories = auxiliary_directories | {"rawdata", "derivatives"}
    for directory in (rawdata, derivatives):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError(f"Expected a regular directory: {directory}")

    other_directories = (
        [
            path
            for path in sorted(root.iterdir())
            if path.is_dir()
            and path.name not in protected_directories
            and not path.name.startswith((".", "sub-"))
        ]
        if not derivatives.exists()
        else []
    )
    if any(path.is_symlink() for path in other_directories):
        raise ValueError(f"Cannot reorganize symlink directories in {root}.")

    raw_children = sorted(rawdata.iterdir()) if rawdata.exists() else []
    for child in raw_children:
        destination = root / child.name
        if (
            destination.exists()
            or destination.is_symlink()
            or (child.name == "derivatives" and other_directories)
        ):
            raise FileExistsError(
                f"Cannot flatten rawdata: destination exists: {destination}"
            )

    if other_directories:
        derivatives.mkdir()
        for directory in other_directories:
            directory.rename(derivatives / directory.name)
    for child in raw_children:
        child.rename(root / child.name)
    if rawdata.exists():
        rawdata.rmdir()

    participants = root / "participants.tsv"
    if participants.is_symlink() or (
        participants.exists() and not participants.is_file()
    ):
        raise ValueError(f"Expected a regular participants table: {participants}")
    if not participants.exists():
        subjects: set[str] = set()
        subject_pattern = re.compile(r"^sub-([A-Za-z0-9]+)(?=_|\.|$)")
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if relative.parts[0] in auxiliary_directories | {"derivatives"} or any(
                part.startswith(".") for part in relative.parts
            ):
                continue
            match = subject_pattern.match(path.name)
            if match:
                subjects.add(f"sub-{match.group(1)}")
        if not subjects:
            raise ValueError(f"No BIDS subject labels found to create {participants}.")

        with participants.open("x", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
            writer.writerow(["participant_id", "age", "sex"])
            writer.writerows((subject, "N/A", "N/A") for subject in sorted(subjects))

    return root


prepare_provider_dataset = prepare_dataset

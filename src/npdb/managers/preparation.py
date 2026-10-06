import csv
import re
import stat
import tarfile
import zipfile
from pathlib import Path


def extract_archive(archive_path: Path, destination: Path) -> Path:
    if archive_path.is_symlink():
        raise ValueError(f"Refusing to unpack symlink archive: {archive_path}")
    if any(path.is_symlink() for path in (destination, *destination.parents)):
        raise ValueError(f"Refusing symlink destination: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()

    def validate_member(name: str) -> None:
        target = (destination / name).resolve()
        if (
            "\\" in name
            or not target.is_relative_to(destination)
            or target == archive_path.resolve()
        ):
            raise ValueError(f"Unsafe member '{name}' in archive '{archive_path}'.")

    if archive_path.name.lower().endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                validate_member(member.filename)
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError(
                        f"Unsafe link '{member.filename}' in archive '{archive_path}'."
                    )
            archive.extractall(destination)
    else:
        with tarfile.open(archive_path) as archive:
            for member in archive.getmembers():
                validate_member(member.name)
                if not (member.isfile() or member.isdir()):
                    raise ValueError(
                        f"Unsafe member '{member.name}' in archive '{archive_path}'."
                    )
            archive.extractall(destination, filter="data")
    return destination


def unpack_provider_archives(fetched: str | Path) -> Path:
    fetched_path = Path(fetched)
    root = fetched_path if fetched_path.is_dir() else fetched_path.parent
    candidates = sorted(root.rglob("*")) if fetched_path.is_dir() else [fetched_path]
    tar_suffixes = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
    archives = [
        path
        for path in candidates
        if path.is_file()
        and (
            path.name.lower().endswith(".zip")
            or path.name.lower().endswith(tar_suffixes)
        )
    ]
    for archive_path in archives:
        extract_archive(archive_path, archive_path.parent)
        archive_path.unlink()
    return root


def prepare_provider_dataset(root: Path) -> Path:
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

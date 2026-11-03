import csv
import re
import stat
import struct
import tarfile
import zipfile
from pathlib import Path


def _repair_legacy_zip_offsets(archive: zipfile.ZipFile, archive_path: Path) -> None:
    members = archive.infolist()
    modulus = 1 << 32
    if not members or members[0].header_offset < modulus:
        return
    bias = members[0].header_offset
    if bias % modulus:
        return
    with archive_path.open("rb") as stream:
        stream.seek(bias)
        if stream.read(4) == b"PK\x03\x04":
            return
        offsets = []
        previous = -1
        wrap = 0
        for member in members:
            offset = (member.header_offset - bias) % modulus + wrap
            if offset <= previous:
                wrap += modulus
                offset += modulus
            if offset >= archive.start_dir:
                raise zipfile.BadZipFile("Invalid legacy ZIP member offset.")
            stream.seek(offset)
            header = stream.read(30)
            if len(header) != 30 or header[:4] != b"PK\x03\x04":
                raise zipfile.BadZipFile("Invalid legacy ZIP local header.")
            fields = struct.unpack("<4s5H3L2H", header)
            name = stream.read(fields[-2]).decode(
                "utf-8" if fields[2] & 0x800 else "cp437"
            )
            if name != member.orig_filename:
                raise zipfile.BadZipFile("Legacy ZIP local filename mismatch.")
            offsets.append(offset)
            previous = offset
    # Some pre-ZIP64 writers wrap offsets at 4 GiB. Validate all local headers
    # before adjusting offsets and the stdlib's overlapping-entry guard.
    for index, (member, offset) in enumerate(zip(members, offsets)):
        member.header_offset = offset
        member._end_offset = (
            offsets[index + 1] if index + 1 < len(offsets) else archive.start_dir
        )


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
            _repair_legacy_zip_offsets(archive, archive_path)
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

"""Provider archive handling and compatibility imports for generic BIDS layout."""

import stat
import struct
import tarfile
import zipfile
from pathlib import Path

from bids_repair.dataset import find_dataset_root, prepare_provider_dataset


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

    # Validate legacy 4 GiB-wrapped offsets before changing the overlap guard.
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

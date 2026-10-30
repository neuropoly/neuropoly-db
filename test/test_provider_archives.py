import importlib
import io
import stat
import struct
import tarfile
import zipfile
from unittest.mock import Mock, patch

import pytest

from npdb.providers.model import Provider
from npdb.providers.preparation import unpack_provider_archives


def test_provider_preparation_hooks_are_ordered(tmp_path):
    provider = Provider()
    extracted = tmp_path / "extracted"
    prepared = tmp_path / "bids"

    with (
        patch.object(provider, "unpack", return_value=extracted) as unpack,
        patch.object(provider, "prepare", return_value=prepared) as prepare,
    ):
        assert provider.prepare_fetched(tmp_path) == prepared

    unpack.assert_called_once_with(tmp_path)
    prepare.assert_called_once_with(extracted)


def test_provider_default_hooks_prepare_an_archive(tmp_path):
    with zipfile.ZipFile(tmp_path / "rawdata.zip", "w") as archive:
        archive.writestr("rawdata/sub-01/anat/sub-01_T1w.nii.gz", b"image")

    assert Provider().prepare_fetched(tmp_path) == tmp_path
    assert (tmp_path / "sub-01/anat/sub-01_T1w.nii.gz").read_bytes() == b"image"
    assert (tmp_path / "participants.tsv").exists()


@pytest.mark.parametrize(
    "suffix, mode",
    [
        (".tar", "w"),
        (".tar.gz", "w:gz"),
        (".tgz", "w:gz"),
        (".tar.bz2", "w:bz2"),
        (".tar.xz", "w:xz"),
    ],
)
def test_unpack_tar_variants(tmp_path, suffix, mode):
    archive_path = tmp_path / f"dataset{suffix}"
    with tarfile.open(archive_path, mode) as archive:
        member = tarfile.TarInfo("dataset/participants.tsv")
        member.size = len(b"data")
        archive.addfile(member, io.BytesIO(b"data"))

    assert unpack_provider_archives(tmp_path) == tmp_path
    assert (tmp_path / "dataset/participants.tsv").read_bytes() == b"data"
    assert not archive_path.exists()


def test_zip_extracts_beside_archive_and_preserves_ordinary_files(tmp_path):
    folder = tmp_path / "download"
    folder.mkdir()
    archive_path = folder / "dataset.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("participants.tsv", b"data")
        archive.writestr("nested.zip", b"leave packed")
    ordinary = tmp_path / "README"
    ordinary.write_text("readme")

    unpack_provider_archives(tmp_path)

    assert (folder / "participants.tsv").read_bytes() == b"data"
    assert (folder / "nested.zip").read_bytes() == b"leave packed"
    assert ordinary.read_text() == "readme"
    assert not archive_path.exists()


def test_single_archive_fetch_returns_parent(tmp_path):
    archive_path = tmp_path / "dataset.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("participants.tsv", b"data")

    assert unpack_provider_archives(archive_path) == tmp_path
    assert not archive_path.exists()


@pytest.mark.parametrize("name", ["../outside.tsv", "/outside.tsv", "dataset.zip"])
def test_unsafe_zip_is_retained(tmp_path, name):
    archive_path = tmp_path / "dataset.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(name, b"data")

    with pytest.raises(ValueError, match="Unsafe member"):
        unpack_provider_archives(tmp_path)

    assert archive_path.exists()


def test_tar_links_are_rejected(tmp_path):
    archive_path = tmp_path / "dataset.tar"
    with tarfile.open(archive_path, "w") as archive:
        member = tarfile.TarInfo("link")
        member.type = tarfile.SYMTYPE
        member.linkname = "../outside"
        archive.addfile(member)

    with pytest.raises(ValueError, match="Unsafe member"):
        unpack_provider_archives(tmp_path)

    assert archive_path.exists()


def test_corrupt_archive_is_retained(tmp_path):
    archive_path = tmp_path / "dataset.zip"
    archive_path.write_bytes(b"invalid")

    with pytest.raises(zipfile.BadZipFile):
        unpack_provider_archives(tmp_path)

    assert archive_path.exists()


def test_extraction_failure_retains_archive(tmp_path):
    archive_path = tmp_path / "dataset.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("participants.tsv", b"data")

    with (
        patch.object(zipfile.ZipFile, "extractall", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        unpack_provider_archives(tmp_path)

    assert archive_path.exists()


def test_zip_symlink_is_rejected(tmp_path):
    archive_path = tmp_path / "dataset.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        member = zipfile.ZipInfo("link")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(member, "../outside")

    with pytest.raises(ValueError, match="Unsafe link"):
        unpack_provider_archives(tmp_path)

    assert archive_path.exists()


def test_existing_symlink_cannot_redirect_extraction(tmp_path):
    root = tmp_path / "download"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "dataset").symlink_to(outside, target_is_directory=True)
    archive_path = root / "dataset.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dataset/participants.tsv", b"data")

    with pytest.raises(ValueError, match="Unsafe member"):
        unpack_provider_archives(root)

    assert archive_path.exists()
    assert not (outside / "participants.tsv").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_legacy_zip_with_wrapped_32_bit_offsets(tmp_path, corrupt):
    original = io.BytesIO()
    with zipfile.ZipFile(original, "w") as archive:
        archive.writestr("first.tsv", b"first")
        archive.writestr("second.tsv", b"second")
        archive.writestr("third.tsv", b"third")
        second_offset = archive.getinfo("second.tsv").header_offset
        third_offset = archive.getinfo("third.tsv").header_offset
        central_offset = archive.start_dir

    data = bytearray(original.getvalue())
    physical_second = (1 << 32) - 64
    physical_third = (1 << 32) + 64
    physical_central = physical_third + central_offset - third_offset
    central = central_offset
    for offset in (0, physical_second, physical_third % (1 << 32)):
        struct.pack_into("<L", data, central + 42, offset)
        name_size, extra_size, comment_size = struct.unpack_from(
            "<3H", data, central + 28
        )
        central += 46 + name_size + extra_size + comment_size
    struct.pack_into("<L", data, len(data) - 6, physical_central % (1 << 32))
    archive_path = tmp_path / "legacy.zip"
    # A sparse gap reproduces 4-GiB offset overflow without storing 4 GiB.
    with archive_path.open("wb") as stream:
        stream.write(data[:second_offset])
        stream.seek(physical_second)
        stream.write(data[second_offset:third_offset])
        stream.seek(physical_third)
        stream.write(data[third_offset:])
        if corrupt:
            stream.seek(30)
            stream.write(b"X")

    if corrupt:
        with pytest.raises(zipfile.BadZipFile, match="filename mismatch"):
            unpack_provider_archives(tmp_path)
        assert archive_path.exists()
        assert not (tmp_path / "first.tsv").exists()
    else:
        unpack_provider_archives(tmp_path)
        assert (tmp_path / "first.tsv").read_bytes() == b"first"
        assert (tmp_path / "second.tsv").read_bytes() == b"second"
        assert (tmp_path / "third.tsv").read_bytes() == b"third"
        assert not archive_path.exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_provider_unpacks_before_conversion(tmp_path, corrupt):
    module = importlib.import_module("npdb.cli.cli")
    archive_path = tmp_path / "dataset.zip"
    if corrupt:
        archive_path.write_bytes(b"invalid")
    else:
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("participants.tsv", b"data")
    provider = Mock()
    provider.provider_name = "figshare"
    provider.fetch.side_effect = (
        lambda *args, **kwargs: Provider().prepare_fetched(tmp_path)
    )

    def convert(**kwargs):
        assert kwargs["input_dir"] == tmp_path
        assert (tmp_path / "participants.tsv").read_bytes() == b"data"
        assert not archive_path.exists()

    with (
        patch.object(module.ProviderFactory, "create", return_value=provider),
        patch("npdb.cli.cli.local2bagel", side_effect=convert) as conversion,
    ):
        kwargs = dict(
            output=tmp_path / "out",
            online_url="https://example.com",
            access_type="public",
            mode="manual",
            phenotype_dict=None,
            headless=True,
            timeout=300,
            artifacts_dir=None,
            ai_provider=None,
            ai_model=None,
            header_map=None,
            extend_modalities=True,
        )
        if corrupt:
            with pytest.raises(zipfile.BadZipFile):
                module._provider_call("figshare", "123", **kwargs)
            conversion.assert_not_called()
            assert archive_path.exists()
        else:
            module._provider_call("figshare", "123", **kwargs)
            conversion.assert_called_once()

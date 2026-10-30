import hashlib
import importlib
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

zget = pytest.importorskip("zenodo_get.zget")

from npdb.cli.helpers import extend_bids_description  # noqa: E402
from npdb.providers.zenodo import ZenodoProvider  # noqa: E402

WRAPPER = "spine-generic-data-multi-subject-2b5f8f7"
KEY = "spine-generic/data-multi-subject-r20201130.zip"


def github_release_zip() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            f"{WRAPPER}/dataset_description.json",
            json.dumps({"Name": "spine-generic", "BIDSVersion": "1.6.0"}),
        )
        archive.writestr(
            f"{WRAPPER}/participants.tsv", "participant_id\tage\tsex\nsub-01\t30\tF\n"
        )
        archive.writestr(f"{WRAPPER}/sub-01/anat/sub-01_T2w.nii.gz", b"image")
        archive.writestr(
            f"{WRAPPER}/derivatives/labels/sub-01/anat/sub-01_T2w_seg.nii.gz", b"seg"
        )
    return stream.getvalue()


class FakeZenodo:
    """Fake only the HTTP boundary; zenodo_get's real workflow runs."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.downloads: list[str] = []
        self.tokens: set[str | None] = set()

    def metadata(self, record_id, sandbox, access_token, timeout, exceptions):
        self.tokens.add(access_token)
        return {
            "metadata": {"title": "Spine Generic Public Database (Multi-Subject)"},
            "files": [
                {
                    "key": key,
                    "size": len(content),
                    "checksum": f"md5:{hashlib.md5(content).hexdigest()}",
                    "links": {"self": f"https://zenodo.org/api/{key}"},
                }
                for key, content in self.files.items()
            ],
        }

    def download_file(self, url, out, verbosity, timeout):
        key = next(key for key in self.files if url.split("?")[0].endswith(key))
        self.downloads.append(key)
        Path(out).write_bytes(self.files[key])
        return out


@pytest.fixture
def zenodo(monkeypatch):
    def install(files):
        fake = FakeZenodo(files)
        monkeypatch.setattr(zget, "_fetch_record_metadata", fake.metadata)
        monkeypatch.setattr(zget, "download_file", fake.download_file)
        return fake

    return install


def test_github_release_archive_is_extracted_to_wrapped_bids_root(tmp_path, zenodo):
    fake = zenodo({KEY: github_release_zip()})
    output = tmp_path / "out" / "zenodo_4299140"

    root = ZenodoProvider(cache_dir=tmp_path / "cache").fetch("4299140", output)

    assert root == output / "spine-generic" / WRAPPER
    assert json.loads((root / "dataset_description.json").read_text())["Name"]
    assert (root / "sub-01/anat/sub-01_T2w.nii.gz").read_bytes() == b"image"
    assert (root / "derivatives/labels/sub-01/anat/sub-01_T2w_seg.nii.gz").is_file()
    assert not list(output.rglob("*.zip"))
    assert fake.downloads == [KEY]


def test_cached_archive_is_not_downloaded_again(tmp_path, zenodo):
    fake = zenodo({KEY: github_release_zip()})
    provider = ZenodoProvider(token="secret", cache_dir=tmp_path / "cache")

    provider.fetch("4299140", tmp_path / "first")
    root = provider.fetch("4299140", tmp_path / "second")

    assert fake.downloads == [KEY]
    assert fake.tokens == {"secret"}
    assert (root / "dataset_description.json").is_file()
    assert (tmp_path / "cache/zenodo/4299140" / KEY).is_file()


def test_corrupt_cache_entry_is_redownloaded(tmp_path, zenodo):
    fake = zenodo({KEY: github_release_zip()})
    cached = tmp_path / "cache/zenodo/4299140" / KEY
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"truncated")

    ZenodoProvider(cache_dir=tmp_path / "cache").fetch("4299140", tmp_path / "o")

    assert fake.downloads == [KEY]


def test_stale_cache_files_are_not_staged(tmp_path, zenodo):
    zenodo({KEY: github_release_zip()})
    stale = tmp_path / "cache/zenodo/4299140/old-release.zip"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    output = tmp_path / "out"

    ZenodoProvider(cache_dir=tmp_path / "cache").fetch("4299140", output)

    assert not (output / "old-release.zip").exists()
    assert not (output / "old-release").exists()


@pytest.mark.parametrize("key", ["../escape.zip", "/abs.zip", "a\\b.zip"])
def test_unsafe_file_keys_are_rejected_before_download(tmp_path, zenodo, key):
    fake = zenodo({key: b"x"})
    with pytest.raises(ValueError, match="Unsafe Zenodo file entry"):
        ZenodoProvider(cache_dir=tmp_path / "cache").fetch("1", tmp_path / "o")
    assert fake.downloads == []
    assert not (tmp_path / "escape.zip").exists()


def test_cache_and_output_overlap_is_rejected(tmp_path, zenodo):
    zenodo({KEY: github_release_zip()})
    with pytest.raises(ValueError, match="must not overlap"):
        ZenodoProvider(cache_dir=tmp_path).fetch("1", tmp_path / "zenodo")


def test_default_cache_follows_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv("NP_NPDB_CACHE_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert ZenodoProvider().cache_dir == tmp_path / "npdb" / "downloads"


def test_cli_converts_from_wrapped_root_inside_output(tmp_path, zenodo, monkeypatch):
    zenodo({KEY: github_release_zip()})
    monkeypatch.setenv("NP_NPDB_CACHE_DIR", str(tmp_path / "cache"))
    module = importlib.import_module("npdb.cli.cli")
    output = tmp_path / "out" / "zenodo"
    seen = {}

    def convert(**kwargs):
        seen.update(kwargs)
        extend_bids_description(
            kwargs["input_dir"].name,
            str(kwargs["input_dir"]),
            kwargs["online_url"],
            kwargs["access_type"],
        )

    with patch("npdb.cli.cli.local2bagel", side_effect=convert):
        module._provider_call(
            "zenodo",
            "4299140",
            output=output,
            online_url="https://zenodo.org/records/4299140",
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

    assert seen["input_dir"] == output / "zenodo_4299140" / "spine-generic" / WRAPPER
    assert seen["output"] == output

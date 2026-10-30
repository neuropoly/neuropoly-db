import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from npdb.providers import openneuro as openneuro_module
from npdb.providers.openneuro import OpenNeuroProvider

FILES = [
    {"filename": "dataset_description.json", "directory": False, "annexed": False},
    {"filename": "participants.tsv", "directory": False, "annexed": False},
    {"filename": ".bidsignore", "directory": False, "annexed": False},
    {"filename": ".gitattributes", "directory": False, "annexed": False},
    {"filename": "sub-01", "directory": True, "annexed": False},
    {"filename": "sub-01/anat/sub-01_T1w.json", "directory": False, "annexed": False},
    {"filename": "sub-01/anat/sub-01_T1w.nii.gz", "directory": False, "annexed": True},
    {"filename": "sub-01/func/sub-01_task-a[1]_bold.json", "directory": False},
]


@pytest.fixture
def downloads(monkeypatch):
    calls = []

    def download(**kwargs):
        calls.append(kwargs)
        target = Path(kwargs["target_dir"])
        names = [p.lstrip("/").replace("[[]", "[") for p in kwargs.get("include") or []]
        for name in names or [
            "dataset_description.json",
            "sub-01/anat/sub-01_T1w.json",
        ]:
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            (target / name).write_text("{}")

    module = SimpleNamespace(download=download)
    monkeypatch.setattr(openneuro_module, "require_module", lambda *_, **__: module)
    return calls


@pytest.fixture
def graphql(monkeypatch):
    queries = []

    def fake(self, query, variables):
        queries.append(variables)
        if "latestSnapshot" in query:
            return {"dataset": {"latestSnapshot": {"tag": "1.0.0"}}}
        return {"snapshot": {"files": FILES}}

    monkeypatch.setattr(OpenNeuroProvider, "_graphql", fake)
    return queries


def test_metadata_only_downloads_git_files_and_links_annexed(
    tmp_path, downloads, graphql
):
    root = OpenNeuroProvider().fetch("ds000001", tmp_path / "out")

    assert graphql == [
        {"datasetId": "ds000001"},
        {"datasetId": "ds000001", "tag": "1.0.0"},
    ]
    assert len(downloads) == 1
    assert downloads[0]["tag"] == "1.0.0"
    assert downloads[0]["include"] == [
        "/dataset_description.json",
        "/participants.tsv",
        "/.bidsignore",
        "/sub-01/anat/sub-01_T1w.json",
        "/sub-01/func/sub-01_task-a[[]1]_bold.json",
    ]

    placeholder = root / "sub-01/anat/sub-01_T1w.nii.gz"
    assert placeholder.is_symlink()
    assert not placeholder.exists()
    assert os.readlink(placeholder) == (
        "../../.openneuro-unfetched/sub-01/anat/sub-01_T1w.nii.gz"
    )
    assert not (root / ".gitattributes").exists()


def test_metadata_only_uses_pinned_tag_and_is_rerunnable(tmp_path, downloads, graphql):
    provider = OpenNeuroProvider()

    provider.fetch("ds000001", tmp_path / "out", tag="2.0.0")
    provider.fetch("ds000001", tmp_path / "out", tag="2.0.0")

    assert graphql == [{"datasetId": "ds000001", "tag": "2.0.0"}] * 2
    assert all(call["tag"] == "2.0.0" for call in downloads)


def test_full_download_fetches_everything(tmp_path, downloads, graphql):
    OpenNeuroProvider().fetch("ds000001", tmp_path / "out", metadata_only=False)

    assert graphql == []
    assert downloads == [
        {"dataset": "ds000001", "tag": None, "target_dir": str(tmp_path / "out")}
    ]


@pytest.mark.parametrize("name", ["/etc/passwd", "../escape.json", "a\\b.json"])
def test_unsafe_snapshot_paths_are_rejected(tmp_path, downloads, monkeypatch, name):
    monkeypatch.setattr(
        OpenNeuroProvider,
        "_graphql",
        lambda *_: {"snapshot": {"files": [{"filename": name, "annexed": False}]}},
    )

    with pytest.raises(ValueError, match="Unsafe OpenNeuro file path"):
        OpenNeuroProvider().fetch("ds000001", tmp_path / "out", tag="1.0.0")

    assert downloads == []


def test_missing_snapshot_is_reported(tmp_path, downloads, monkeypatch):
    monkeypatch.setattr(OpenNeuroProvider, "_graphql", lambda *_: {"snapshot": None})

    with pytest.raises(ValueError, match="ds000001:9.9.9"):
        OpenNeuroProvider().fetch("ds000001", tmp_path / "out", tag="9.9.9")

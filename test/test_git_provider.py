import subprocess
from pathlib import Path

import pytest

from npdb.managers.git import GitProviderManager


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        (
            "https://github.com/courtois-neuromod/anat/tree/main",
            "courtois-neuromod_anat_main",
        ),
        ("https://github.com/org/repo.git", "org_repo"),
        ("github.com/org/repo/tree/0491c0b3", "org_repo_0491c0b3"),
        ("https://github.com/org/repo/tree/feature/x", "org_repo_feature/x"),
        ("https://example.com/repo", "repo"),
    ],
)
def test_git_dataset_id(identifier: str, expected: str) -> None:
    assert GitProviderManager(repo_url="").dataset_id(identifier) == expected


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_git_fetch_checks_out_full_repository(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "sub-01" / "anat").mkdir(parents=True)
    (work / "sub-01" / "anat" / "sub-01_T1w.json").write_text("{}")
    (work / "dataset_description.json").write_text('{"Name": "test"}')
    _git("init", "-q", "-b", "main", cwd=work)
    _git("add", ".", cwd=work)
    _git(
        "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i",
        cwd=work,
    )
    bare = tmp_path / "repo.git"
    _git("clone", "-q", "--bare", str(work), str(bare), cwd=tmp_path)

    manager = GitProviderManager(repo_url="")
    out = manager.fetch(f"file://{bare}/tree/main", tmp_path / "out")

    assert (out / "dataset_description.json").is_file()
    assert (out / "sub-01" / "anat" / "sub-01_T1w.json").is_file()
    assert (out / "participants.tsv").read_text().splitlines()[1].startswith(
        "sub-01\t"
    )

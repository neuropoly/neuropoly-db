import csv
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx

def read_tsv(tsv_path: Path) -> list[dict]:
    with open(tsv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError("TSV file is empty or has no header row")
        rows = list(reader)
    if not rows:
        raise ValueError("TSV file contains no data rows")
    return rows


def fetch_url(url: str, dest: Path, timeout: int = 300) -> tuple[bool, str]:
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as r:
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        return True, f"Downloaded: {dest.name}"
    except Exception as exc:
        return False, str(exc)


def is_http_url(value: str) -> bool:
    is_http = value.startswith(("http://", "https://"))
    is_git = value.endswith(".git") or "/tree/" in value
    return is_http and not is_git


def normalize_repo_url_for_git(repo_url: str) -> str:
    parsed = urlparse(repo_url if "://" in repo_url else f"https://{repo_url}")
    repo_path = parsed.path.rstrip("/")
    tree_idx = repo_path.find("/tree/")
    if tree_idx != -1:
        repo_path = repo_path[:tree_idx]
    if not repo_path.endswith(".git"):
        repo_path += ".git"
    return f"{parsed.scheme}://{parsed.netloc}{repo_path}"


def repo_has_git_annex(gitea_manager, repo_url: str) -> bool:
    git_url = normalize_repo_url_for_git(repo_url)
    cmd = (
        ["git"]
        + gitea_manager.git_http_config()
        + ["ls-remote", "--heads", git_url, "refs/heads/git-annex"]
    )

    try:
        stdout, _ = gitea_manager._run_git(
            cmd,
            env=gitea_manager.git_env(),
            context=f"probe git-annex metadata branch for '{repo_url}'",
        )
    except RuntimeError:
        return False

    return bool(stdout.strip())


def looks_like_non_git_repo_error(message: str) -> bool:
    lowered = message.lower()
    patterns = [
        "not a git repository",
        "does not appear to be a git repository",
        "fatal: repository",
        "repository not found",
    ]
    return any(p in lowered for p in patterns)


def extend_bids_description(
    dataset: str, local_clone: str, url: str, access_type: str = "restricted"
):
    """
    Extend the dataset_description.json file using NeuroBagel standard.
    See : https://neurobagel.org/user_guide/dataset_description/#editable-template
    """
    desc_path = Path(local_clone) / "dataset_description.json"
    with open(desc_path, "r") as f:
        description = json.load(f)

    description["Name"] = dataset

    if not description.get("Keywords"):
        description["Keywords"] = [dataset]

    description["RepositoryURL"] = f"{url}"

    description["AccessInstructions"] = (
        "Refer to the access link provided with the repository."
    )
    description["AccessLink"] = description["RepositoryURL"]
    description["AccessType"] = access_type

    return description

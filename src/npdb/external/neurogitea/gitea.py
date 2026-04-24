import os
import subprocess
from base64 import b64encode
from pathlib import Path
from urllib.parse import urlparse

import gitea as gt_client

from npdb.managers.model import Manager


class GiteaManager(Manager):
    def __init__(self, url: str, user: str, token: str, ssl_verify: bool = True):
        # Normalise the URL: strip protocol if present, remember it.
        # NP_GITEA_APP_URL may or may not include a scheme; both forms are
        # accepted and produce the same behaviour.
        if "://" in url:
            _parsed = urlparse(url)
            self._proto = _parsed.scheme          # "https" or "http"
            self.host = _parsed.netloc          # "data.neuro.polymtl.ca"
        else:
            self._proto = "https"                 # sensible default
            self.host = url.split("/")[0]       # strip any trailing path

        self._http_base = f"{self._proto}://{self.host}"

        self.client = gt_client.Gitea(
            gitea_url=self._http_base, token_text=token, verify=ssl_verify)
        self.git_auth = b64encode(
            f"{user}:{token}".encode("utf-8")).decode("ascii")

    def git_http_config(self):
        config = {
            "extraHeader": f"Authorization: Basic {self.git_auth}",
            "sslVerify": str(self.client.requests.verify).lower()
        }
        return [c for k, v in config.items() for c in ["-c", f"http.{k}={v}"]]

    def _to_ssh_url(self, http_url: str) -> str:
        """Convert a Gitea HTTP(S) repository URL to SSH form.

        Example::

            https://data.neuro.polymtl.ca/datasets/whole-spine
            → git@data.neuro.polymtl.ca:datasets/whole-spine.git

        The hostname is always taken from ``self.host`` so that URLs whose
        host differs from the configured server (e.g. after a redirect) are
        corrected automatically.
        """
        parsed = urlparse(
            http_url if "://" in http_url else f"https://{http_url}"
        )
        path = parsed.path.rstrip("/")
        if not path.endswith(".git"):
            path += ".git"
        return f"git@{self.host}:{path.lstrip('/')}"

    def _git_env(self) -> dict:
        """Return an environment dict with interactive git prompts disabled."""
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def _run_git(self, cmd: list[str], env: dict, context: str) -> None:
        """Run a git command, raising RuntimeError with full detail on failure."""
        try:
            subprocess.run(cmd, check=True, capture_output=True,
                           env=env, timeout=3600)
        except subprocess.CalledProcessError as e:
            detail = (
                f"Command: {' '.join(e.cmd)}\n"
                f"Stdout: {e.stdout.decode(errors='replace')}\n"
                f"Stderr: {e.stderr.decode(errors='replace')}"
            )
            raise RuntimeError(f"{context} failed.\n{detail}") from e

    def clone_sparse(self, repo_url: str, sparse_paths: list[str], dest: Path) -> None:
        """
        Shallow sparse clone fetching one or more directory paths in one shot.

        The repository is cloned once into *dest* with ``--filter=blob:none``
        and ``--no-checkout``, then sparse-checkout (cone mode) is initialised
        and set to *all* requested paths before a single checkout is performed.
        This avoids re-cloning for multiple subjects from the same repository.

        If *dest* already contains a valid git repository the clone step is
        skipped and only the sparse-checkout set is updated before re-checking
        out (idempotent, safe to call repeatedly).

        Authentication is injected via http.extraHeader.

        Args:
            repo_url: Repository URL without ``.git`` suffix
                      (e.g. ``https://data.neuro.polymtl.ca/datasets/whole-spine``).
            sparse_paths: One or more directory paths inside the repo to check
                          out (e.g. ``["sub-amuAP", "sub-amuLJ"]``).
            dest: Local destination directory for the clone.

        Raises:
            RuntimeError: If any git sub-command fails.
            ValueError: If *sparse_paths* is empty.
        """
        if not sparse_paths:
            raise ValueError("sparse_paths must contain at least one path")

        # Build the HTTPS clone URL from the normalised host so that
        # protocol mismatches in the TSV (e.g. bare host or http vs https)
        # are corrected automatically.
        parsed_repo = urlparse(
            repo_url if "://" in repo_url else f"https://{repo_url}"
        )
        repo_path = parsed_repo.path.rstrip("/")
        git_url = f"{self._http_base}{repo_path}.git"
        env = self._git_env()
        git = ["git"] + self.git_http_config()

        # Clone only if the destination is not already a git repo.
        if not (dest / ".git").exists():
            dest.mkdir(parents=True, exist_ok=True)
            self._run_git(
                git + ["clone", "--filter=blob:none", "--no-checkout",
                       "--depth=1", git_url, str(dest)],
                env=env,
                context=f"clone '{repo_url}'",
            )

        # (Re-)configure sparse-checkout with the full set of paths.
        self._run_git(
            git + ["-C", str(dest), "sparse-checkout", "init", "--cone"],
            env=env,
            context=f"sparse-checkout init in '{dest}'",
        )
        self._run_git(
            git + ["-C", str(dest), "sparse-checkout", "set"] + sparse_paths,
            env=env,
            context=f"sparse-checkout set {sparse_paths} in '{dest}'",
        )
        self._run_git(
            git + ["-C", str(dest), "checkout"],
            env=env,
            context=f"checkout in '{dest}'",
        )

    def annex_get(self, repo_dir: Path, paths: list[str] | None = None) -> None:
        """
        Fetch git-annex file content for the checked-out sparse paths.

        Sequence of operations (order matters):

        1. **Switch origin to SSH** — Gitea's plain HTTP transport doesn't
           speak the git-annex protocol; SSH works because Gitea exposes the
           ``git-annex-shell`` command.  We read the current HTTPS origin URL
           and replace it with its SSH equivalent before any annex commands run.

        2. **Fetch the git-annex metadata branch** — ``git clone --depth=1``
           only fetches the default branch.  The ``git-annex`` branch stores
           per-file location logs (which UUID/remote has each key).  Without
           it, ``git annex whereis`` and ``git annex get`` see "0 copies" for
           every file.  We fetch it explicitly as a remote-tracking ref.

        3. **``git annex init``** — registers this clone as a local repository
           and installs the smudge/clean filters.

        4. **Unset ``annex-ignore``** — ``git annex init`` on a shallow clone
           auto-sets ``remote.origin.annex-ignore = true`` because the shallow
           remote looks incomplete.  We force it to ``false``; the SSH remote
           does serve annex objects.

        5. **``git annex merge``** — merges the remote tracking ``git-annex``
           branch into the local ``git-annex`` branch so that location logs are
           available locally.

        6. **``git annex get``** — downloads the actual file content for each
           requested path.

        Args:
            repo_dir: Root of the cloned git-annex repository.
            paths: Subdirectories or files inside *repo_dir* to fetch.
                   Defaults to everything checked out (``["."]``).

        Raises:
            RuntimeError: If any git-annex sub-command fails.
        """
        if paths is None:
            paths = ["."]

        env = self._git_env()

        # 1. Switch origin to SSH (git-annex requires SSH transport for Gitea).
        origin_proc = subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
            capture_output=True, text=True, env=env,
        )
        if origin_proc.returncode == 0:
            ssh_url = self._to_ssh_url(origin_proc.stdout.strip())
            self._run_git(
                ["git", "-C", str(repo_dir),
                 "remote", "set-url", "origin", ssh_url],
                env=env,
                context=f"switch origin to SSH in '{repo_dir}'",
            )

        # 2. Fetch the git-annex metadata branch as a proper tracking ref.
        #    git clone --depth=1 only fetches the HEAD branch; the git-annex
        #    branch (location logs) must be fetched explicitly.
        self._run_git(
            ["git", "-C", str(repo_dir), "fetch", "origin",
             "refs/heads/git-annex:refs/remotes/origin/git-annex"],
            env=env,
            context=f"fetch git-annex branch in '{repo_dir}'",
        )

        # 3. Initialise git-annex in the local clone.
        self._run_git(
            ["git", "-C", str(repo_dir), "annex", "init"],
            env=env,
            context=f"git annex init in '{repo_dir}'",
        )

        # 4. Unset annex-ignore: init on a shallow clone sets this to true.
        self._run_git(
            ["git", "-C", str(repo_dir),
             "config", "remote.origin.annex-ignore", "false"],
            env=env,
            context=f"unset annex-ignore in '{repo_dir}'",
        )

        # 5. Merge remote location logs into the local git-annex branch.
        self._run_git(
            ["git", "-C", str(repo_dir), "annex", "merge"],
            env=env,
            context=f"git annex merge in '{repo_dir}'",
        )

        # 6. Download actual file content.
        self._run_git(
            ["git", "-C", str(repo_dir), "annex", "get"] + paths,
            env=env,
            context=f"git annex get {paths} in '{repo_dir}'",
        )

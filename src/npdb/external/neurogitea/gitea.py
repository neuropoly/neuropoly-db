import json
import os
import shlex
import subprocess
import tempfile
from base64 import b64encode
from collections.abc import Callable
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import gitea as gt_client
from tenacity import retry, stop_after_attempt, wait_exponential

from npdb.managers.model import Manager


class GiteaManager(Manager):
    def __init__(self, url: str, user: str, token: str, ssl_verify: bool = True):
        # Normalise the URL: strip protocol if present, remember it.
        # NP_GITEA_APP_URL may or may not include a scheme; both forms are
        # accepted and produce the same behaviour.
        if "://" in url:
            _parsed = urlparse(url)
            self._proto = _parsed.scheme  # "https" or "http"
            self.host = _parsed.netloc  # "data.neuro.polymtl.ca"
        else:
            self._proto = "https"  # sensible default
            self.host = url.split("/")[0]  # strip any trailing path

        self._http_base = f"{self._proto}://{self.host}"

        self.client = gt_client.Gitea(
            gitea_url=self._http_base, token_text=token, verify=ssl_verify
        )
        self.git_auth = b64encode(f"{user}:{token}".encode("utf-8")).decode("ascii")
        self.verbose: bool = False

    def git_http_config(self):
        config = {
            "extraHeader": f"Authorization: Basic {self.git_auth}",
            "sslVerify": str(self.client.requests.verify).lower(),
        }
        return [c for k, v in config.items() for c in ["-c", f"http.{k}={v}"]]

    def _to_ssh_url(self, http_url: str) -> str:
        """Convert a Gitea HTTP(S) or SSH repository URL to SSH form.

        Supported input formats::

            https://data.neuro.polymtl.ca/datasets/whole-spine
            https://data.neuro.polymtl.ca/datasets/whole-spine/tree/<ref>
            git@data.neuro.polymtl.ca:datasets/whole-spine.git  (idempotent)

        The hostname is always taken from ``self.host`` so that URLs whose
        host differs from the configured server (e.g. after a redirect) are
        corrected automatically.  The ``/tree/<ref>`` suffix is stripped so
        that the resulting SSH URL always points at the repository root.
        """
        if http_url.startswith("git@"):
            # Already SSH format: git@host:owner/repo[.git]
            # Split on the first ':' to get the repo path.
            path = http_url.split(":", 1)[1]
        else:
            parsed = urlparse(http_url if "://" in http_url else f"https://{http_url}")
            path = parsed.path.rstrip("/")

        # Strip /tree/<ref> if present (Gitea commit/tree URLs).
        tree_idx = path.find("/tree/")
        if tree_idx != -1:
            path = path[:tree_idx]

        path = path.rstrip("/")
        if not path.endswith(".git"):
            path += ".git"
        return f"git@{self.host}:{path.lstrip('/')}"

    def _git_env(self) -> dict:
        """Return an environment dict with interactive git prompts disabled."""
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        # Prevent SSH from hanging when the server host key is not yet in
        # known_hosts.  accept-new silently accepts genuinely new keys but
        # still rejects changed keys (TOFU).  BatchMode=yes makes SSH fail
        # immediately rather than block if any interactive prompt is needed.
        env.setdefault(
            "GIT_SSH_COMMAND",
            "ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes",
        )
        return env

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True
    )
    def _run_git(
        self,
        cmd: list[str],
        env: dict,
        context: str,
        output_callback: Callable[[str], None] | None = None,
        progress_callback: Callable[[str, float, int, int], None] | None = None,
    ) -> None:
        """Run a git command, raising RuntimeError with full detail on failure.

        If *output_callback* is provided, stdout lines are forwarded to it after
        the command completes.

        If *progress_callback* is provided, the command is run with
        ``capture_output=True`` and stdout is parsed for JSON progress events
        emitted by ``git annex get --json --json-progress``:

        - Progress event (``"percentdone"`` key): calls
          ``progress_callback(file, pct, bytes_done, bytes_total)``.
        - Completion event (``"success": true``): calls
          ``progress_callback(file, 100.0, 0, 0)``.
        - Malformed or non-JSON lines are silently skipped.
        """
        if self.verbose:
            print(f"+ {shlex.join(cmd)}", flush=True)

        try:
            result = subprocess.run(
                cmd, check=True, capture_output=True, text=True, env=env, timeout=3600
            )
        except subprocess.CalledProcessError as e:
            detail = (
                f"Command: {' '.join(e.cmd)}\n"
                f"Stdout: {e.stdout}\n"
                f"Stderr: {e.stderr}"
            )
            raise RuntimeError(f"{context} failed.\n{detail}") from e

        if output_callback is not None:
            for line in result.stdout.splitlines():
                output_callback(line)

        if progress_callback is not None:
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                if "percentdone" in event and "action" in event:
                    action = event.get("action", {})
                    file = action.get("file", "unknown")
                    pct = float(event.get("percentdone", 0))
                    bytes_done = int(event.get("bytesdone", 0))
                    bytes_total = int(event.get("bytestotal", 0))
                    progress_callback(file, pct, bytes_done, bytes_total)
                elif event.get("success") is True and "file" in event:
                    file = event.get("file", "unknown")
                    progress_callback(file, 100.0, 0, 0)

    def clone_sparse(
        self,
        repo_url: str,
        sparse_paths: list[str],
        dest: Path,
        step_callback: Callable[[str, int, int], None] | None = None,
    ) -> None:
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
            repo_url: Repository URL without ``.git`` suffix.  May be a plain
                      repo URL (e.g. ``…/datasets/whole-spine``) **or** a Gitea
                      tree URL that pins a specific commit/ref
                      (e.g. ``…/datasets/whole-spine/tree/0491c0b3…``).  The
                      ``/tree/<ref>`` segment is stripped before cloning and the
                      ref is passed to ``git checkout`` so that the working tree
                      matches the exact snapshot requested.
            sparse_paths: One or more directory paths inside the repo to check
                          out (e.g. ``["sub-amuAP", "sub-amuLJ"]``).
            dest: Local destination directory for the clone.
            step_callback: Optional callback invoked with description before each git operation.

        Raises:
            RuntimeError: If any git sub-command fails.
            ValueError: If *sparse_paths* is empty.
        """
        if not sparse_paths:
            raise ValueError("sparse_paths must contain at least one path")

        # Build the HTTPS clone URL from the normalised host so that
        # protocol mismatches in the TSV (e.g. bare host or http vs https)
        # are corrected automatically.
        #
        # repo_url may include a Gitea /tree/<ref> suffix
        # (e.g. ".../whole-spine/tree/0491c0b3...").  Strip it to obtain the
        # actual repository path and remember the pinned ref separately.
        parsed_repo = urlparse(repo_url if "://" in repo_url else f"https://{repo_url}")
        full_path = parsed_repo.path.rstrip("/")
        tree_marker = "/tree/"
        tree_idx = full_path.find(tree_marker)

        if tree_idx != -1:
            pinned_ref: str | None = full_path[tree_idx + len(tree_marker) :]
            repo_path = full_path[:tree_idx]
        else:
            pinned_ref = None
            repo_path = full_path

        git_url = f"{self._http_base}{repo_path}.git"
        env = self._git_env()
        git = ["git"] + self.git_http_config()

        # Extract dataset name from repo_url path
        dataset_name = repo_path.split("/")[-1] if repo_path else "repository"

        # Clone only if the destination is not already a git repo.
        if not (dest / ".git").exists():
            dest.mkdir(parents=True, exist_ok=True)

            if step_callback:
                step_callback(f"Cloning {dataset_name}...", 0, 4)

            clone_cmd = git + ["clone", "--filter=blob:none", "--no-checkout"]
            # --depth=1 fetches only HEAD; omit it when a specific commit is
            # pinned so that the full history is available for checkout.
            if pinned_ref is None:
                clone_cmd.append("--depth=1")

            clone_cmd += [git_url, str(dest)]
            self._run_git(
                clone_cmd,
                env=env,
                context=f"clone '{repo_url}'",
            )

        # (Re-)configure sparse-checkout with the full set of paths.
        if step_callback:
            step_callback("Initializing sparse checkout...", 1, 4)

        self._run_git(
            git + ["-C", str(dest), "sparse-checkout", "init", "--cone"],
            env=env,
            context=f"sparse-checkout init in '{dest}'",
        )

        if step_callback:
            step_callback(f"Configuring paths: {', '.join(sparse_paths)}", 2, 4)

        self._run_git(
            git + ["-C", str(dest), "sparse-checkout", "set"] + sparse_paths,
            env=env,
            context=f"sparse-checkout set {sparse_paths} in '{dest}'",
        )

        if step_callback:
            step_callback("Checking out files...", 3, 4)

        checkout_cmd = git + ["-C", str(dest), "checkout"]
        if pinned_ref is not None:
            checkout_cmd.append(pinned_ref)

        self._run_git(
            checkout_cmd,
            env=env,
            context=f"checkout in '{dest}'",
        )

        if step_callback:
            step_callback("Sparse clone complete...", 4, 4)

    def get_main_branch_head_commit(self, repo_url: str):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.clone_sparse(repo_url, sparse_paths=["."], dest=Path(tmpdir))

            result = subprocess.run(
                ["git", "-C", tmpdir, "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            )

            return result.stdout.strip()

    def annex_get(
        self,
        repo_dir: Path,
        paths: list[str] | None = None,
        step_callback: Callable[[str, int, int], None] | None = None,
        progress_callback: Callable[[str, float, int, int], None] | None = None,
    ) -> None:
        """
        Fetch git-annex file content for the checked-out sparse paths.

        Sequence of operations (order matters):

        1. **Fetch the git-annex metadata branch** — ``git clone --depth=1``
           only fetches the default branch.  The ``git-annex`` branch stores
           per-file location logs (which UUID/remote has each key).  Without
           it, ``git annex whereis`` and ``git annex get`` see "0 copies" for
           every file.  We fetch it explicitly as a remote-tracking ref over
           HTTPS (token auth) before switching to SSH.

        2. **Switch origin to SSH** — Gitea's git-annex content transfer
           requires SSH because Gitea exposes ``git-annex-shell`` only over
           SSH.  Done *after* the git-annex branch fetch so that plain-git
           operations (which work over HTTPS with a token) are not affected.

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
           requested path. If progress_callback is provided, JSON progress events
           are parsed and reported via the callback.

        Args:
            repo_dir: Root of the cloned git-annex repository.
            paths: Subdirectories or files inside *repo_dir* to fetch.
                   Defaults to everything checked out (``["."]``).
            progress_callback: Optional callback(file, pct, bytes_done, bytes_total)
                              for per-file download progress.

        Raises:
            RuntimeError: If any git-annex sub-command fails.
        """
        if paths is None:
            paths = ["."]

        env = self._git_env()
        git = ["git"] + self.git_http_config()

        # 1. Fetch the git-annex metadata branch as a proper tracking ref over
        #    HTTPS (token auth).  git clone --depth=1 only fetches the HEAD
        #    branch; the git-annex branch (location logs) must be fetched
        #    explicitly.  This must happen before switching origin to SSH
        #    because the fetch is a plain git operation — no git-annex-shell
        #    needed — and HTTPS + token works here whereas SSH keys are
        #    required for the SSH transport.
        if step_callback:
            step_callback("Fetching git-annex metadata...", 0, 5)

        self._run_git(
            git
            + [
                "-C",
                str(repo_dir),
                "fetch",
                "origin",
                "refs/heads/git-annex:refs/remotes/origin/git-annex",
            ],
            env=env,
            context=f"fetch git-annex branch in '{repo_dir}'",
        )

        # 2. Switch origin to SSH.  git-annex requires SSH transport for
        #    Gitea because content transfer uses git-annex-shell over SSH.
        #    Done after the git-annex branch fetch so that plain-git operations
        #    (which work over HTTPS) are not affected.
        if step_callback:
            step_callback("Configuring remote...", 1, 5)

        get_url_cmd = ["git", "-C", str(repo_dir), "remote", "get-url", "origin"]
        if self.verbose:
            print(f"+ {shlex.join(get_url_cmd)}", flush=True)
        origin_proc = subprocess.run(
            get_url_cmd, capture_output=True, text=True, env=env
        )
        if origin_proc.returncode == 0:
            ssh_url = self._to_ssh_url(origin_proc.stdout.strip())
            self._run_git(
                ["git", "-C", str(repo_dir), "remote", "set-url", "origin", ssh_url],
                env=env,
                context=f"switch origin to SSH in '{repo_dir}'",
            )

        # 3. Initialise git-annex in the local clone.
        if step_callback:
            step_callback("Initializing git-annex...", 2, 5)

        self._run_git(
            ["git", "-C", str(repo_dir), "annex", "init"],
            env=env,
            context=f"git annex init in '{repo_dir}'",
        )

        # 4. Unset annex-ignore: init on a shallow clone sets this to true.
        self._run_git(
            [
                "git",
                "-C",
                str(repo_dir),
                "config",
                "remote.origin.annex-ignore",
                "false",
            ],
            env=env,
            context=f"unset annex-ignore in '{repo_dir}'",
        )

        # 5. Merge remote location logs into the local git-annex branch.
        if step_callback:
            step_callback("Merging remote location logs...", 3, 5)

        self._run_git(
            ["git", "-C", str(repo_dir), "annex", "merge"],
            env=env,
            context=f"git annex merge in '{repo_dir}'",
        )

        # 6. Download actual file content.
        if step_callback:
            step_callback("Downloading file content...", 4, 5)

        cmd = ["git", "-C", str(repo_dir), "annex", "get"] + paths
        if progress_callback:
            cmd.extend(["--json", "--json-progress"])

        self._run_git(
            cmd,
            env=env,
            context=f"git annex get {paths} in '{repo_dir}'",
            progress_callback=progress_callback,
        )

        if step_callback:
            step_callback("Download complete.", 5, 5)

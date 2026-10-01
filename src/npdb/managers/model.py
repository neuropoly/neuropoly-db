import os
import shlex
import subprocess
import tempfile
import threading
from abc import ABC, abstractmethod
from base64 import b64encode
from enum import Enum
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, ClassVar, List, Sequence
from urllib.parse import urlparse

from tenacity import retry, stop_after_attempt, wait_exponential

from npdb.cli.observers import DownloadObserver


class ProviderName(str, Enum):
    GIT = "git"
    KAGGLE = "kaggle"
    MENDELEY = "mendeley"
    MIDRC = "midrc"
    OPENNEURO = "openneuro"
    ZENODO = "zenodo"
    FIGSHARE = "figshare"


class Manager(ABC):
    def __init__(self):
        self._download_observers: list[DownloadObserver] = []

    @property
    @abstractmethod
    def datasets(self) -> Sequence[Any]:
        pass

    def add_download_observer(self, observer: DownloadObserver) -> None:
        """Register an observer to receive download progress notifications."""
        self._download_observers.append(observer)

    def _notify_file_progress(
        self, repo: str, file: str, bytes_done: int, bytes_total: int
    ) -> None:
        for obs in self._download_observers:
            obs.on_file_progress(repo, file, bytes_done, bytes_total)

    def _notify_file_complete(self, repo: str, file: str) -> None:
        for obs in self._download_observers:
            obs.on_file_complete(repo, file)

    def _notify_repo_step(
        self, repo: str, step: str, step_num: int, total_steps: int
    ) -> None:
        for obs in self._download_observers:
            obs.on_repo_step(repo, step, step_num, total_steps)

    def _notify_repo_done(self, repo: str, success: bool) -> None:
        for obs in self._download_observers:
            obs.on_repo_done(repo, success)

    def _notify_repo_error(self, repo: str, message: str) -> None:
        for obs in self._download_observers:
            obs.on_repo_error(repo, message)


class ProviderManager(Manager):
    provider_name: ClassVar[ProviderName] = ProviderName.GIT
    requires_cache = False
    access_type = "public"

    def __init__(self, cache_dir: str | Path | None = None, **_: Any):
        super().__init__()
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None

    @property
    def datasets(self) -> Sequence[Any]:
        return []

    def ensure_cache_dir(self, *, required: bool | None = None) -> Path | None:
        if required is None:
            required = self.requires_cache
        if required:
            if self.cache_dir is None:
                cache = os.environ.get("NP_NPDB_CACHE_DIR")
                if cache:
                    self.cache_dir = Path(cache)
            if self.cache_dir is None:
                raise ValueError(
                    "A cache directory is required for this provider. "
                    "Pass --cache-dir or set NP_NPDB_CACHE_DIR. "
                    "This download may be large."
                )
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            return self.cache_dir
        return self.cache_dir

    def describe(self, identifier: str) -> tuple[str, str]:
        return identifier, self.access_type

    def fetch(self, identifier: str, output_dir: str | Path, **kwargs: Any) -> Path:
        _ = identifier
        _ = output_dir
        _ = kwargs
        raise NotImplementedError


class GitManager(Manager):
    def __init__(self, user: str, token: str, ssl_verify: bool = True):
        super().__init__()
        self._user = user
        self._token = token
        self._ssl_verify = ssl_verify

    @property
    def datasets(self) -> Sequence[Any]:
        return []

    def git_http_config(self) -> list[str]:
        git_auth = b64encode(f"{self._user}:{self._token}".encode("utf-8")).decode(
            "ascii"
        )
        return [
            "-c",
            f"http.extraHeader=Authorization: Basic {git_auth}",
            "-c",
            f"http.sslVerify={str(self._ssl_verify).lower()}",
        ]

    def git_env(self) -> dict:
        """Return an environment dict with interactive git prompts disabled."""
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        # Force-disable GUI/askpass credential prompts in non-interactive runs.
        # VS Code exports GIT_ASKPASS by default; do not preserve it.
        env["GIT_ASKPASS"] = "/bin/echo"
        env["SSH_ASKPASS"] = "/bin/echo"
        env["GCM_INTERACTIVE"] = "never"
        env.pop("VSCODE_GIT_ASKPASS_NODE", None)
        env.pop("VSCODE_GIT_ASKPASS_MAIN", None)
        env.pop("VSCODE_GIT_ASKPASS_EXTRA_ARGS", None)
        # Prevent SSH from hanging when the server host key is not yet in
        # known_hosts.  accept-new silently accepts genuinely new keys but
        # still rejects changed keys (TOFU).  BatchMode=yes makes SSH fail
        # immediately rather than block if any interactive prompt is needed.
        env.setdefault(
            "GIT_SSH_COMMAND",
            "ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes",
        )
        return env

    def get_main_branch_head_commit(self, repo_url: str):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.clone_sparse(repo_url, sparse_paths=["."], dest=Path(tmpdir))

            stdout, _ = self._run_git(
                ["git", "-C", tmpdir, "rev-parse", "HEAD"],
                env=self.git_env(),
                context=f"rev-parse in '{tmpdir}'",
            )

            return stdout.strip()

    def clone_sparse(
        self,
        repo_url: str,
        sparse_paths: list[str],
        dest: Path,
    ) -> None:
        """
        Shallow sparse clone fetching one or more directory paths in one shot.

        The repository is cloned once into *dest* with ``--filter=blob:none``
        and ``--no-checkout``, then sparse-checkout (cone mode) is initialised
        and set to *all* requested paths before a single checkout is performed.

        If *dest* already contains a valid git repository the clone step is
        skipped and only the sparse-checkout set is updated before re-checking
        out (idempotent, safe to call repeatedly).

        Authentication is injected via http.extraHeader.

        Args:
            repo_url: Repository URL, with or without ``.git`` suffix.  May be a plain
                      repo URL (e.g. ``…/org/repo``) **or** a git
                      tree URL that pins a specific commit/ref
                      (e.g. ``…/org/repo/tree/0491c0b3…``).  The
                      ``/tree/<ref>`` segment is stripped before cloning and the
                      ref is passed to ``git checkout`` so that the working tree
                      matches the exact snapshot requested.
            sparse_paths: One or more directory paths inside the repo to check out.
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
        #
        # repo_url may include a Gitea /tree/<ref> suffix
        # (e.g. ".../whole-spine/tree/0491c0b3...").  Strip it to obtain the
        # actual repository path and remember the pinned ref separately.
        parsed_repo = urlparse(repo_url if "://" in repo_url else f"https://{repo_url}")
        base = f"{parsed_repo.scheme}://{parsed_repo.netloc}"
        full_path = parsed_repo.path.rstrip("/")
        tree_marker = "/tree/"
        tree_idx = full_path.find(tree_marker)

        if tree_idx != -1:
            pinned_ref: str | None = full_path[tree_idx + len(tree_marker) :]
            repo_path = full_path[:tree_idx]
        else:
            pinned_ref = None
            repo_path = full_path

        repo_path = repo_path.removesuffix(".git")
        git_url = f"{base}{repo_path}.git"
        env = self.git_env()
        git = ["git"] + self.git_http_config()

        # Extract repository name from repo_url path
        repo_name = repo_path.split("/")[-1] if repo_path else "repository"

        # Clone only if the destination is not already a git repo.
        if not (dest / ".git").exists():
            dest.mkdir(parents=True, exist_ok=True)

            self._notify_repo_step(repo_name, f"Cloning {repo_name}…", 0, 4)
            clone_cmd = git + ["clone", "--filter=blob:none", "--no-checkout"]
            # --depth=1 fetches only HEAD; omit it when a specific commit is
            # pinned so that the full history is available for checkout.
            if pinned_ref is None:
                clone_cmd.append("--depth=1")

            clone_cmd += [git_url, str(dest)]
            self._run_git(
                clone_cmd,
                env=env,
                context=f"clone '{git_url}'",
            )

        # (Re-)configure sparse-checkout with the full set of paths.
        self._notify_repo_step(repo_name, "Configuring sparse checkout…", 1, 4)
        self._run_git(
            git + ["-C", str(dest), "sparse-checkout", "init", "--no-cone"],
            env=env,
            context=f"sparse-checkout init in '{dest}'",
        )

        self._notify_repo_step(repo_name, "Setting sparse paths…", 2, 4)
        self._run_git(
            git
            + [
                "-C",
                str(dest),
                "sparse-checkout",
                "set",
            ]  # "--skip-checks"]
            + sparse_paths,
            env=env,
            context=f"sparse-checkout set {sparse_paths} in '{dest}'",
        )

        self._notify_repo_step(repo_name, "Checking out…", 3, 4)
        checkout_cmd = git + ["-C", str(dest), "checkout"]
        if pinned_ref is not None:
            checkout_cmd.append(pinned_ref)

        self._run_git(
            checkout_cmd,
            env=env,
            context=f"checkout in '{dest}'",
        )

        self._notify_repo_step(repo_name, "Sparse checkout complete", 4, 4)

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True
    )
    def _run_git(
        self,
        cmd: list[str],
        env: dict,
        context: str,
        line_parser_hook: Callable[[str], None] | None = None,
        timeout: int = 3600,
    ) -> tuple[str, str]:
        """Run a git command via Popen and optionally parse output lines live.

        Args:
            cmd: Command and arguments to execute.
            env: Process environment.
            context: Human-readable context for error reporting.
            line_parser_hook: Optional callback invoked for each stdout/stderr
                line as it arrives.
            timeout: Maximum time in seconds before the process is killed.

        Returns:
            Tuple of ``(stdout_text, stderr_text)``.

        Raises:
            RuntimeError: If process exits non-zero or times out.
        """
        if getattr(self, "verbose", False):
            print(f"+ {shlex.join(cmd)}", flush=True)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        assert proc.stdout is not None
        assert proc.stderr is not None

        output_q: Queue[tuple[str, str]] = Queue()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def _pump(stream, label: str) -> None:
            for raw in stream:
                output_q.put((label, raw.rstrip("\n")))
            output_q.put((label, "__EOF__"))

        t_out = threading.Thread(
            target=_pump, args=(proc.stdout, "stdout"), daemon=True
        )
        t_err = threading.Thread(
            target=_pump, args=(proc.stderr, "stderr"), daemon=True
        )
        t_out.start()
        t_err.start()

        eof_seen = {"stdout": False, "stderr": False}

        try:
            while not all(eof_seen.values()):
                try:
                    label, line = output_q.get(timeout=0.1)
                except Empty:
                    continue

                if line == "__EOF__":
                    eof_seen[label] = True
                    continue

                if label == "stdout":
                    stdout_lines.append(line)
                else:
                    stderr_lines.append(line)

                if line_parser_hook is not None:
                    line_parser_hook(line)

            return_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            raise RuntimeError(f"{context} failed.\nTimeout: {e}") from e

        stdout_text = "\n".join(stdout_lines)
        stderr_text = "\n".join(stderr_lines)

        if return_code != 0:
            detail = (
                f"Command: {' '.join(cmd)}\n"
                f"Stdout: {stdout_text}\n"
                f"Stderr: {stderr_text}"
            )
            raise RuntimeError(f"{context} failed.\n{detail}")

        return stdout_text, stderr_text


class BagelDB:
    def __init__(self, jsonld_root: str):
        self.root = jsonld_root


class NeurobagelManager(Manager):
    def __init__(self, jsonld: str):
        self.db = BagelDB(jsonld)

    @property
    def datasets(self) -> List[str]:
        return os.listdir(self.db.root)

    def load_dataset(self, dataset: str, destination_path: str, light: bool = False):
        """Stub — implemented by subclasses (e.g. BagelNeuroPolyMTL)."""

    def extend_description(
        self, dataset: str, dataset_path: str, extra_keywords: list[str] = []
    ) -> dict:
        """Stub — implemented by subclasses (e.g. BagelNeuroPolyMTL)."""
        return {}

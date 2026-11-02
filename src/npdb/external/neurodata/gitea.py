import json
from pathlib import Path
from urllib.parse import urlparse

import gitea as gt_client

from npdb.managers.model import GitManager


class GiteaManager(GitManager):
    def __init__(self, url: str, user: str, token: str, ssl_verify: bool = True):
        super().__init__(user, token, ssl_verify)

        # Normalise the URL: strip protocol if present, remember it.
        # NP_GITEA_APP_URL may or may not include a scheme; both forms are
        # accepted and produce the same behaviour.
        if "://" in url:
            _parsed = urlparse(url)
            self._proto = _parsed.scheme  # "https" or "http"
            self._host = _parsed.netloc  # "data.neuro.polymtl.ca"
        else:
            self._proto = "https"  # sensible default
            self._host = url.split("/")[0]  # strip any trailing path

        self._http_base = f"{self._proto}://{self._host}"

        self.client = gt_client.Gitea(
            gitea_url=self._http_base, token_text=self._token, verify=self._ssl_verify
        )

        self.verbose: bool = False

    @property
    def host(self) -> str:
        """Compatibility alias used by tests and older call sites."""
        return self._host

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
        return f"git@{self._host}:{path.lstrip('/')}"

    def _parse_annex_event_line(self, repo_name: str, line: str) -> None:
        """Parse one git-annex JSON event line and notify observers if relevant."""
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return

        if "percentdone" in event and "action" in event:
            action = event.get("action", {})
            file = action.get("file", "unknown")
            bytes_done = int(event.get("bytesdone", 0))
            bytes_total = int(event.get("bytestotal", 0))
            self._notify_file_progress(repo_name, file, bytes_done, bytes_total)
        elif event.get("success") is True and "file" in event:
            file = event.get("file", "unknown")
            self._notify_file_complete(repo_name, file)

    def _annex_include_args(self, includes: list[str] | None) -> list[str]:
        """Build git-annex include predicates as separate CLI args."""
        if not includes:
            return []
        args: list[str] = []
        for i, inc in enumerate(includes):
            if i > 0:
                args.append("--or")
            args.extend(["--include", inc])
        return args

    def annex_get(
        self,
        repo_dir: Path,
        paths: list[str] | None = None,
        repo_name: str | None = None,
        data_class: str | None = None,
        includes: list[str] | None = None,
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
              requested path. JSON progress events are parsed and forwarded to
              registered :class:`~npdb.cli.observers.DownloadObserver` instances.

        Args:
            repo_dir: Root of the cloned git-annex repository.
            paths: Subdirectories or files inside *repo_dir* to fetch.
                   Defaults to everything checked out (``["."]``).
            repo_name: Repository label used for observer notifications. If not
                       provided, defaults to ``repo_dir.name``.
            data_class: Optional data class label (e.g. "raw", "derivatives") to
                        include in observer notifications.
            includes: Optional list of git-annex include patterns to limit the
                      set of files considered for download.  If not provided, all
                      files in the requested paths are considered.

        Raises:
            RuntimeError: If any git-annex sub-command fails.
        """
        if paths is None:
            paths = ["."]

        if repo_name is None:
            repo_name = repo_dir.name

        env = self.git_env()
        git = ["git"] + self.git_http_config()

        # 1. Fetch the git-annex metadata branch as a proper tracking ref over
        #    HTTPS (token auth).  git clone --depth=1 only fetches the HEAD
        #    branch; the git-annex branch (location logs) must be fetched
        #    explicitly.  This must happen before switching origin to SSH
        #    because the fetch is a plain git operation — no git-annex-shell
        #    needed — and HTTPS + token works here whereas SSH keys are
        #    required for the SSH transport.
        self._notify_repo_step(repo_name, "Fetching git-annex metadata…", 0, 5)
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
        self._notify_repo_step(repo_name, "Configuring remote for git-annex…", 1, 5)
        get_url_cmd = ["git", "-C", str(repo_dir), "remote", "get-url", "origin"]
        try:
            origin_stdout, _ = self._run_git(
                get_url_cmd,
                env=env,
                context=f"get origin URL in '{repo_dir}'",
            )
            ssh_url = self._to_ssh_url(origin_stdout.strip())
            self._run_git(
                ["git", "-C", str(repo_dir), "remote", "set-url", "origin", ssh_url],
                env=env,
                context=f"switch origin to SSH in '{repo_dir}'",
            )
        except RuntimeError:
            # If origin URL cannot be read, continue with current remote config.
            pass

        # 3. Initialise git-annex in the local clone.
        self._notify_repo_step(repo_name, "Initializing git-annex…", 2, 5)
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
        self._notify_repo_step(repo_name, "Merging remote location logs…", 3, 5)
        self._run_git(
            ["git", "-C", str(repo_dir), "annex", "merge"],
            env=env,
            context=f"git annex merge in '{repo_dir}'",
        )

        # 6. Download actual file content.
        data_label = f" ({data_class}) " if data_class else ""
        self._notify_repo_step(
            repo_name, f"Downloading file content{data_label}…", 4, 5
        )

        cmd = [
            "git",
            "-C",
            str(repo_dir),
            "annex",
            "get",
            "--json",
            "--json-progress",
        ]
        cmd += self._annex_include_args(includes)
        cmd += paths

        annex_line_parser = None
        if self._download_observers:
            annex_line_parser = lambda line: self._parse_annex_event_line(
                repo_name, line
            )

        self._run_git(
            cmd,
            env=env,
            context=f"git annex get {paths} in '{repo_dir}'",
            line_parser_hook=annex_line_parser,
        )

        self._notify_repo_step(repo_name, "Download complete", 5, 5)

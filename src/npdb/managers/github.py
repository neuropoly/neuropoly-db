import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from npdb.managers.model import GitManager


class GithubManager(GitManager):
    """
    Git and git-annex operations for public GitHub repositories
    """

    def __init__(self, ssl_verify: bool = True):
        # GitHub public downloads do not use Gitea credentials
        super().__init__("", "", ssl_verify, auth_host="")
        self.verbose = False

    @property
    def datasets(self) -> list[str]:
        return []

    def _parse_annex_event_line(self, repo_name: str, line: str) -> None:
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

    @staticmethod
    def _normalize_subject_path(repo_url: str, sparse_path: str) -> str:
        sparse_path = sparse_path.lstrip("/")
        repo_name = Path(urlparse(repo_url).path).name.removesuffix(".git")
        if sparse_path == repo_name or sparse_path.startswith(f"{repo_name}/"):
            sparse_path = sparse_path.removeprefix(f"{repo_name}/")
        return sparse_path

    def _annex_include_args(self, includes: list[str] | None) -> list[str]:
        if not includes:
            return []

        args: list[str] = []
        for index, include in enumerate(includes):
            if index:
                args.append("--or")
            args.extend(["--include", include])
        return args

    def annex_get(self, repo_dir: Path, paths: list[str] | None = None, repo_name: str | None = None,
                  data_class: str | None = None, includes: list[str] | None = None) -> None:
        """
        Download git-annex content from a public repository over HTTPS
        """

        if paths is None:
            paths = ["."]
        if repo_name is None:
            repo_name = repo_dir.name

        env = self.git_env()

        self._notify_repo_step(repo_name, "Fetching git-annex metadata…", 0, 5)
        self._run_git(
            ["git", "-C", str(repo_dir), "fetch", "origin",
             "refs/heads/git-annex:refs/remotes/origin/git-annex"],
            env=env, context=f"fetch git-annex branch in '{repo_dir}'")

        self._notify_repo_step(repo_name, "Initializing git-annex…", 1, 5)
        self._run_git(
            ["git", "-C", str(repo_dir), "annex", "init"],
            env=env, context=f"git annex init in '{repo_dir}'")

        self._run_git(
            ["git", "-C", str(repo_dir), "config",
             "remote.origin.annex-ignore", "false"],
            env=env, context=f"unset annex-ignore in '{repo_dir}'")

        self._notify_repo_step(repo_name, "Merging remote location logs…", 2, 5)
        self._run_git(
            ["git", "-C", str(repo_dir), "annex", "merge"],
            env=env, context=f"git annex merge in '{repo_dir}'")

        data_label = f" ({data_class}) " if data_class else ""
        self._notify_repo_step(repo_name, f"Downloading file content{data_label}…", 3, 5)

        cmd = ["git", "-C", str(repo_dir), "annex", "get", "--json", "--json-progress"]
        cmd += self._annex_include_args(includes)
        cmd += paths

        annex_line_parser = None
        if self._download_observers:
            annex_line_parser = lambda line: self._parse_annex_event_line(repo_name, line)

        self._run_git(cmd, env=env, context=f"git annex get {paths} in '{repo_dir}'", line_parser_hook=annex_line_parser)

        self._notify_repo_step(repo_name, "Download complete", 4, 5)

    def download_subjects(self, subjects: list[tuple[str, str, str]], output_dir: Path, use_annex: bool = False,
                          derivatives: bool = True) -> list[tuple[bool, str, str]]:
        """
        Download repository subjects with one sparse clone per repository
        """

        groups: dict[tuple[str, str], list[str]] = defaultdict(list)

        for repo_url, sparse_path, dataset_name in subjects:
            sparse_path = self._normalize_subject_path(repo_url, sparse_path)
            key = (repo_url, dataset_name)

            if sparse_path not in groups[key]:
                groups[key].append(sparse_path)

                if derivatives:
                    groups[key].append(f"derivatives/*/{sparse_path}/*")

        results: list[tuple[bool, str, str]] = []

        for (repo_url, dataset_name), sparse_paths in groups.items():
            dest = output_dir / dataset_name
            label = f"{dataset_name} [{', '.join(sparse_paths)}]"

            try:
                self.clone_sparse(repo_url, sparse_paths, dest)

                if use_annex:
                    includes = [path for path in sparse_paths if path.startswith("derivatives/")]
                    raw_paths = [path for path in sparse_paths if not path.startswith("derivatives/")]

                    self.annex_get(dest, raw_paths, repo_name=dataset_name, data_class="raw")
                    self.annex_get(dest, repo_name=dataset_name, data_class="derivatives", includes=includes)

                results.append((True, label, "OK"))
                self._notify_repo_done(dataset_name, True)
            except RuntimeError as error:
                results.append((False, label, str(error)))
                self._notify_repo_error(dataset_name, str(error))
                self._notify_repo_done(dataset_name, False)

        return results

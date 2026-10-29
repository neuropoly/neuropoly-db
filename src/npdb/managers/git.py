from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

from npdb.managers.model import GitManager, ProviderManager, ProviderName


class GitProviderManager(GitManager, ProviderManager):
    provider_name: ClassVar[ProviderName] = ProviderName.GIT

    def __init__(
        self,
        repo_url: str,
        user: str | None = None,
        token: str | None = None,
        ssl_verify: bool = True,
        cache_dir: str | Path | None = None,
    ):
        GitManager.__init__(self, user or "", token or "", ssl_verify)
        ProviderManager.__init__(self, cache_dir=cache_dir)
        self.repo_url = repo_url

    def describe(self, identifier: str) -> tuple[str, str]:
        return identifier, "public"

    def dataset_id(self, identifier: str) -> str:
        git_url, repo_name, ref = self.parse_repo_url(identifier)
        parents = urlparse(git_url).path.strip("/").split("/")[:-1]
        name = f"{parents[-1]}_{repo_name}" if parents else repo_name
        return f"{name}_{ref}" if ref else name

    def fetch(self, identifier: str, output_dir: str | Path, **kwargs: Any) -> Path:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        # Non-cone sparse patterns are gitignore-style: "/*" checks out everything.
        self.clone_sparse(identifier, sparse_paths=["/*"], dest=output_path)
        return self.prepare_fetched(output_path)

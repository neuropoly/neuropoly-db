from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

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

    def fetch(self, identifier: str, output_dir: str | Path, **kwargs: Any) -> Path:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        self.clone_sparse(identifier, sparse_paths=["."], dest=output_path)
        return output_path

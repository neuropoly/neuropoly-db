from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from npdb.extras import require_module
from npdb.providers.model import Provider, ProviderName


class KaggleProvider(Provider):
    provider_name: ClassVar[ProviderName] = ProviderName.KAGGLE
    requires_cache = True

    def __init__(self, cache_dir: str | Path | None = None, **_: Any):
        super().__init__(cache_dir=cache_dir)

    def fetch(self, identifier: str, output_dir: str | Path, **_: Any) -> Path:
        kagglehub = require_module(
            "kagglehub", extra="kaggle", feature="The Kaggle provider"
        )

        cache = self.ensure_cache_dir(required=True)
        handle = identifier
        path = cache / "kaggle" / handle.replace("/", "__")
        path.mkdir(parents=True, exist_ok=True)

        downloaded = kagglehub.dataset_download(
            handle=handle, path=str(cache / "kaggle")
        )
        target = Path(downloaded)
        if not target.exists():
            target = path
        return self.prepare_fetched(target)

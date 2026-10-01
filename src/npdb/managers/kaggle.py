from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from npdb.managers.model import ProviderManager, ProviderName


class KaggleProviderManager(ProviderManager):
    provider_name: ClassVar[ProviderName] = ProviderName.KAGGLE
    requires_cache = True

    def __init__(self, cache_dir: str | Path | None = None, **_: Any):
        super().__init__(cache_dir=cache_dir)

    def fetch(self, identifier: str, output_dir: str | Path, **_: Any) -> Path:
        try:
            import kagglehub  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment guard
            raise ImportError(
                "The Kaggle provider requires the kaggle extra. "
                "Install it with: uv sync --group kaggle"
            ) from exc

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
        return target

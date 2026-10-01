from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from npdb.managers.model import ProviderManager, ProviderName


class ZenodoProviderManager(ProviderManager):
    provider_name: ClassVar[ProviderName] = ProviderName.ZENODO

    def __init__(
        self, token: str | None = None, cache_dir: str | Path | None = None, **_: Any
    ):
        super().__init__(cache_dir=cache_dir)
        self.token = token or os.environ.get("NP_ZENODO_TOKEN")

    def fetch(self, identifier: str, output_dir: str | Path, **kwargs: Any) -> Path:
        try:
            import zenodo_get  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment guard
            raise ImportError(
                "The Zenodo provider requires the zenodo extra. "
                "Install it with: uv sync --group zenodo"
            ) from exc

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        if self.token:
            os.environ["ZENODO_ACCESS_TOKEN"] = self.token
        zenodo_get.download(record_or_doi=identifier, output_dir=str(output_path))
        return output_path

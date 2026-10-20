from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from npdb.extras import require_module
from npdb.managers.model import ProviderManager, ProviderName


class OpenNeuroProviderManager(ProviderManager):
    provider_name: ClassVar[ProviderName] = ProviderName.OPENNEURO

    def fetch(self, identifier: str, output_dir: str | Path, **kwargs: Any) -> Path:
        openneuro = require_module(
            "openneuro", extra="openneuro", feature="The OpenNeuro provider"
        )

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        openneuro.download(dataset=identifier, target_dir=str(output_path), **kwargs)

        return self.prepare_fetched(output_path)

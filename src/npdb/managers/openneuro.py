from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from npdb.managers.model import ProviderManager, ProviderName


class OpenNeuroProviderManager(ProviderManager):
    provider_name: ClassVar[ProviderName] = ProviderName.OPENNEURO

    def fetch(self, identifier: str, output_dir: str | Path, **kwargs: Any) -> Path:
        try:
            import openneuro  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment guard
            raise ImportError(
                "The OpenNeuro provider requires the openneuro extra. "
                "Install it with: uv sync --group openneuro"
            ) from exc

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        openneuro.download(dataset=identifier, target_dir=str(output_path), **kwargs)

        return output_path

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, ClassVar

import httpx

from npdb.managers.model import ProviderManager, ProviderName


class MIDRCProviderManager(ProviderManager):
    provider_name: ClassVar[ProviderName] = ProviderName.MIDRC
    access_type = "restricted"

    def __init__(
        self,
        credentials_path: str | None = None,
        endpoint: str = "https://data.midrc.org",
        **_: Any,
    ):
        super().__init__(cache_dir=None)
        self.credentials_path = credentials_path or os.environ.get(
            "NP_MIDRC_CREDENTIALS"
        )
        self.endpoint = endpoint or os.environ.get(
            "NP_MIDRC_ENDPOINT", "https://data.midrc.org"
        )

    def fetch(self, identifier: str, output_dir: str | Path, **_: Any) -> Path:
        try:
            from gen3.auth import Gen3Auth
            from gen3.index import Gen3Index
        except ImportError as exc:  # pragma: no cover - environment guard
            raise ImportError(
                "The MIDRC provider requires the midrc extra. "
                "Install it with: uv sync --group midrc"
            ) from exc

        if not self.credentials_path:
            raise ValueError(
                "MIDRC requires a credentials file. Set NP_MIDRC_CREDENTIALS or pass credentials_path. "
                "The file is usually downloaded from your MIDRC profile as credentials.json."
            )

        auth = Gen3Auth(self.endpoint, self.credentials_path)
        index = Gen3Index(self.endpoint, auth)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        manifest_path = Path(identifier)
        if manifest_path.exists():
            with manifest_path.open("r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            for file_entry in (
                manifest.get("records", []) or manifest.get("files", []) or []
            ):
                guid = file_entry.get("guid") or file_entry.get("did")
                if not guid:
                    continue
                item = index.get_record(guid)
                for url in item.get("urls", []):
                    response = httpx.get(url, timeout=60)
                    response.raise_for_status()
                    file_name = item.get("file_name") or str(guid)
                    (output_path / file_name).write_bytes(response.content)
            return output_path

        item = index.get_record(identifier)
        for url in item.get("urls", []):
            response = httpx.get(url, timeout=60)
            response.raise_for_status()
            file_name = item.get("file_name") or str(identifier)
            (output_path / file_name).write_bytes(response.content)
        return output_path

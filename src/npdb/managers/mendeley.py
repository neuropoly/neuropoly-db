from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

import httpx

from npdb.managers.model import ProviderManager, ProviderName


class MendeleyProviderManager(ProviderManager):
    provider_name: ClassVar[ProviderName] = ProviderName.MENDELEY
    access_type = "restricted"

    def __init__(self, access_token: str | None = None, **_: Any):
        super().__init__(cache_dir=None)
        self.access_token = access_token or os.environ.get("NP_MENDELEY_ACCESS_TOKEN")

    def fetch(self, identifier: str, output_dir: str | Path, **_: Any) -> Path:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        url = f"https://api.mendeley.com/datasets/{identifier}"
        response = httpx.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        payload = response.json()
        files = payload.get("files") or []
        if not files:
            raise ValueError(
                f"No files were returned for Mendeley dataset '{identifier}'."
            )

        for file in files:
            file_name = file.get("filename") or file.get("name") or "download.bin"
            download_url = file.get("content_details", {}).get("download_url")
            if not download_url:
                continue
            content = httpx.get(download_url, timeout=60).raise_for_status().content
            (output_path / file_name).write_bytes(content)
        return output_path

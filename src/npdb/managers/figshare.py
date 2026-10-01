from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

import httpx

from npdb.managers.model import ProviderManager, ProviderName


class FigshareProviderManager(ProviderManager):
    provider_name: ClassVar[ProviderName] = ProviderName.FIGSHARE
    access_type = "public"

    def __init__(self, token: str | None = None, **_: Any):
        super().__init__(cache_dir=None)
        self.token = token or os.environ.get("NP_FIGSHARE_TOKEN")

    def fetch(self, identifier: str, output_dir: str | Path, **_: Any) -> Path:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        article_id = identifier
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"

        article = httpx.get(
            f"https://api.figshare.com/v2/articles/{article_id}",
            headers=headers,
            timeout=30,
        )
        article.raise_for_status()
        payload = article.json()
        for file_info in payload.get("files", []):
            url = file_info.get("download_url")
            if not url:
                continue
            response = httpx.get(url, timeout=60)
            response.raise_for_status()
            file_name = (
                file_info.get("name") or f"figshare_{file_info.get('id', 'file')}"
            )
            (output_path / file_name).write_bytes(response.content)
        return output_path

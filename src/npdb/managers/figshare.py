from __future__ import annotations

import os
import re
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

    @staticmethod
    def _doi_from_identifier(identifier: str) -> str | None:
        value = identifier.strip()
        lowered = value.lower()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if lowered.startswith(prefix):
                value = value[len(prefix) :].strip()
                break
        return value if value.lower().startswith("10.") else None

    @staticmethod
    def _search_articles(
        filters: dict[str, Any], headers: dict[str, str]
    ) -> list[dict[str, Any]]:
        articles: list[dict[str, Any]] = []
        page = 1
        while True:
            response = httpx.post(
                "https://api.figshare.com/v2/articles/search",
                json={**filters, "page": page, "page_size": 100},
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            page_articles = response.json()
            if not isinstance(page_articles, list):
                raise ValueError(
                    "Unexpected response while searching Figshare articles."
                )
            articles.extend(page_articles)
            if len(page_articles) < 100:
                return articles
            page += 1

    def _article_ids(self, identifier: str, headers: dict[str, str]) -> list[str | int]:
        doi = self._doi_from_identifier(identifier)
        if doi is None:
            return [identifier]

        collection = re.fullmatch(
            r"10\.6084/m9\.figshare\.c\.([0-9]+)", doi, re.IGNORECASE
        )
        if collection:
            articles = []
            page = 1
            while True:
                response = httpx.get(
                    f"https://api.figshare.com/v2/collections/{collection.group(1)}/articles",
                    params={"page": page, "page_size": 100},
                    headers=headers,
                    timeout=30,
                )
                response.raise_for_status()
                page_articles = response.json()
                if not isinstance(page_articles, list):
                    raise ValueError(
                        "Unexpected response while listing Figshare collection articles."
                    )
                articles.extend(page_articles)
                if len(page_articles) < 100:
                    break
                page += 1
        else:
            articles = self._search_articles({"resource_doi": doi}, headers)
        if not articles and not collection:
            articles = [
                article
                for article in self._search_articles({"search_for": doi}, headers)
                if str(article.get("doi", "")).casefold() == doi.casefold()
            ]
        if not articles:
            raise ValueError(f"No public Figshare articles found for DOI '{doi}'.")
        try:
            return [article["id"] for article in articles]
        except KeyError as exc:
            raise ValueError(
                "Figshare DOI search returned an article without an ID."
            ) from exc

    def fetch(self, identifier: str, output_dir: str | Path, **_: Any) -> Path:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"

        downloaded_names: set[str] = set()
        for article_id in self._article_ids(identifier, headers):
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
                response = httpx.get(url, timeout=60, follow_redirects=True)
                response.raise_for_status()
                file_name = (
                    file_info.get("name") or f"figshare_{file_info.get('id', 'file')}"
                )
                file_path = output_path / file_name
                if file_name in downloaded_names:
                    raise FileExistsError(
                        f"Multiple Figshare files have the name '{file_name}'."
                    )
                file_path.write_bytes(response.content)
                downloaded_names.add(file_name)

        return self.prepare_fetched(output_path)

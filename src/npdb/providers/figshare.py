from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, ClassVar

import httpx

from npdb.providers.model import Provider, ProviderName, default_download_cache


class FigshareProvider(Provider):
    provider_name: ClassVar[ProviderName] = ProviderName.FIGSHARE
    access_type = "public"

    def __init__(
        self, token: str | None = None, cache_dir: str | Path | None = None, **_: Any
    ):
        super().__init__(cache_dir=default_download_cache(cache_dir))
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
        cache = self.ensure_cache_dir()
        self._check_destination(output_path)
        if cache is not None:
            self._check_destination(cache)
            if cache.resolve().is_relative_to(
                output_path.resolve()
            ) or output_path.resolve().is_relative_to(cache.resolve()):
                raise ValueError(
                    "Figshare cache and fetched directory must not overlap."
                )
            cache.mkdir(parents=True, exist_ok=True)
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
                file_name = self._file_name(file_info)
                if file_name in downloaded_names:
                    raise FileExistsError(
                        f"Multiple Figshare files have the name '{file_name}'."
                    )
                size, digest = self._integrity(file_info, file_name)
                target = output_path / file_name
                if cache is not None and size is not None and digest is not None:
                    key = re.sub(r"[^A-Za-z0-9._-]", "_", str(article_id))
                    source = cache / "figshare" / key / file_name
                    self._check_destination(source)
                    if not self._verified(source, size, digest):
                        self._download(url, source, size, digest)
                    self._stage(source, target, size, digest)
                else:
                    self._download(url, target, size, digest)
                downloaded_names.add(file_name)

        return self.prepare_fetched(output_path)

    @staticmethod
    def _file_name(file_info: dict[str, Any]) -> str:
        name = file_info.get("name") or f"figshare_{file_info.get('id', 'file')}"
        if (
            not isinstance(name, str)
            or name in {".", ".."}
            or any(character in name for character in "/\\\x00")
        ):
            raise ValueError(f"Unsafe Figshare file name: {name!r}.")
        return name

    @staticmethod
    def _integrity(
        file_info: dict[str, Any], name: str
    ) -> tuple[int | None, str | None]:
        size = file_info.get("size")
        digest = file_info.get("computed_md5") or file_info.get("supplied_md5")
        if size is not None and (
            not isinstance(size, int) or isinstance(size, bool) or size < 0
        ):
            raise ValueError(f"Invalid Figshare file size for {name!r}.")
        if digest is not None and (
            not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", digest)
        ):
            raise ValueError(f"Invalid Figshare MD5 for {name!r}.")
        return size, digest.lower() if digest is not None else None

    @staticmethod
    def _check_destination(path: Path) -> None:
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError(f"Refusing Figshare symlink destination: {path}.")

    @staticmethod
    def _matches(path: Path, size: int | None, digest: str | None) -> bool:
        if size is not None and path.stat().st_size != size:
            return False
        if digest is None:
            return True

        hasher = hashlib.md5(usedforsecurity=False)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest() == digest

    @classmethod
    def _verified(cls, path: Path, size: int, digest: str) -> bool:
        return path.is_file() and cls._matches(path, size, digest)

    @classmethod
    def _write_verified(
        cls,
        target: Path,
        write: Any,
        size: int | None,
        digest: str | None,
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", suffix=".part", delete=False
        ) as stream:
            partial = Path(stream.name)
            try:
                write(stream)
                stream.close()
                if not cls._matches(partial, size, digest):
                    raise ValueError(
                        f"Figshare file integrity mismatch: {target.name!r}."
                    )
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)

    def _download(
        self, url: str, target: Path, size: int | None, digest: str | None
    ) -> None:
        response = httpx.get(url, timeout=60, follow_redirects=True)
        response.raise_for_status()
        self._write_verified(
            target, lambda stream: stream.write(response.content), size, digest
        )

    def _stage(self, source: Path, target: Path, size: int, digest: str) -> None:
        self._check_destination(target)

        def copy(stream: Any) -> None:
            with source.open("rb") as cached:
                shutil.copyfileobj(cached, stream, length=1024 * 1024)

        self._write_verified(target, copy, size, digest)

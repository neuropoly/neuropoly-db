from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

from npdb.extras import require_module
from npdb.managers.model import (
    ProviderManager,
    ProviderName,
    default_download_cache,
)


class ZenodoProviderManager(ProviderManager):
    provider_name: ClassVar[ProviderName] = ProviderName.ZENODO

    def __init__(
        self, token: str | None = None, cache_dir: str | Path | None = None, **_: Any
    ):
        super().__init__(cache_dir=default_download_cache(cache_dir))
        self.token = token or os.environ.get("NP_ZENODO_TOKEN")

    def fetch(self, identifier: str, output_dir: str | Path, **kwargs: Any) -> Path:
        zenodo_get = require_module(
            "zenodo_get", extra="zenodo", feature="The Zenodo provider"
        )

        output_path = Path(output_dir)
        cache = self.ensure_cache_dir(required=True)
        assert cache is not None
        download_dir = cache / "zenodo" / re.sub(r"[^A-Za-z0-9._-]", "_", identifier)
        for path in (output_path, download_dir):
            self._check_destination(path)
        if download_dir.resolve().is_relative_to(
            output_path.resolve()
        ) or output_path.resolve().is_relative_to(download_dir.resolve()):
            raise ValueError("Zenodo cache and fetched directory must not overlap.")
        output_path.mkdir(parents=True, exist_ok=True)
        download_dir.mkdir(parents=True, exist_ok=True)

        options = {
            "record_or_doi": identifier,
            "access_token": self.token,
            "exceptions_on_failure": True,
        }
        files = self._manifest(zenodo_get, download_dir, options)
        # Files already in the cache with a matching MD5 are not downloaded again.
        zenodo_get.download(output_dir=download_dir, **options)
        for relative, checksum in files:
            self._stage(download_dir / relative, output_path / relative, checksum)
        return self.prepare_fetched(output_path)

    def _manifest(
        self, zenodo_get: Any, download_dir: Path, options: dict[str, Any]
    ) -> list[tuple[PurePosixPath, str]]:
        with tempfile.TemporaryDirectory(dir=download_dir.parent) as manifest_dir:
            zenodo_get.download(output_dir=manifest_dir, md5=True, **options)
            manifest = Path(manifest_dir) / "md5sums.txt"
            lines = manifest.read_text().splitlines() if manifest.is_file() else []
        files = []
        for line in filter(None, lines):
            checksum, _, name = line.partition("  ")
            relative = PurePosixPath(name)
            if (
                not re.fullmatch(r"[0-9a-fA-F]{32}", checksum)
                or not name
                or relative.is_absolute()
                or any(part in {"", ".", ".."} for part in relative.parts)
                or "\\" in name
                or "\x00" in name
            ):
                raise ValueError(f"Unsafe Zenodo file entry: {line!r}.")
            files.append((relative, checksum.lower()))
        if not files:
            raise ValueError("No files were returned for this Zenodo record.")
        return files

    @staticmethod
    def _check_destination(path: Path) -> None:
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError(f"Refusing Zenodo symlink destination: {path}.")

    def _stage(self, source: Path, target: Path, checksum: str) -> None:
        self._check_destination(source)
        self._check_destination(target)
        if self._md5(source) != checksum:
            raise ValueError(f"Zenodo cached file failed MD5 check: {source}.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", suffix=".part", delete=False
        ) as stream:
            partial = Path(stream.name)
            try:
                with source.open("rb") as cached:
                    shutil.copyfileobj(cached, stream, length=1024 * 1024)
                stream.close()
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)

    @staticmethod
    def _md5(path: Path) -> str | None:
        if not path.is_file():
            return None
        digest = hashlib.md5(usedforsecurity=False)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

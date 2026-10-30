from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, ClassVar

import httpx

from npdb.providers.model import (
    Provider,
    ProviderName,
    default_download_cache,
)


class MendeleyProvider(Provider):
    provider_name: ClassVar[ProviderName] = ProviderName.MENDELEY
    access_type = "public"

    def __init__(
        self,
        access_token: str | None = None,
        cache_dir: str | Path | None = None,
        **_: Any,
    ):
        super().__init__(cache_dir=default_download_cache(cache_dir))
        self.access_token = access_token or os.environ.get("NP_MENDELEY_ACCESS_TOKEN")

    def fetch(self, identifier: str, output_dir: str | Path, **_: Any) -> Path:
        output_path = Path(output_dir)
        self._check_destination(output_path)
        output_path.mkdir(parents=True, exist_ok=True)
        cache = self.ensure_cache_dir()
        if cache is not None:
            self._check_destination(cache)
            if cache.resolve().is_relative_to(
                output_path.resolve()
            ) or output_path.resolve().is_relative_to(cache.resolve()):
                raise ValueError(
                    "Mendeley cache and fetched directory must not overlap."
                )
            cache.mkdir(parents=True, exist_ok=True)

        headers = {"Accept": "application/json"}
        response = httpx.get(
            f"https://data.mendeley.com/public-api/datasets/{identifier}",
            headers=headers,
            timeout=30,
            follow_redirects=True,
        )
        self.access_type = "public"
        if response.status_code in {401, 403, 404} and self.access_token:
            response = httpx.get(
                f"https://api.mendeley.com/datasets/{identifier}",
                headers={**headers, "Authorization": f"Bearer {self.access_token}"},
                timeout=30,
                follow_redirects=True,
            )
            self.access_type = "restricted"
        self._check_access(response)
        response.raise_for_status()

        payload = response.json()
        files = payload.get("files") or []
        if not files:
            raise ValueError(
                f"No files were returned for Mendeley dataset '{identifier}'."
            )

        downloaded_names: set[str] = set()
        jobs = []
        for file in files:
            file_name = file.get("filename") or file.get("name")
            if (
                not isinstance(file_name, str)
                or file_name in {"", ".", ".."}
                or "\x00" in file_name
                or "/" in file_name
                or "\\" in file_name
                or ":" in file_name
            ):
                raise ValueError(f"Unsafe Mendeley filename: {file_name!r}.")
            if file_name in downloaded_names:
                raise FileExistsError(f"Duplicate Mendeley filename: {file_name!r}.")
            downloaded_names.add(file_name)
            download_url = (file.get("content_details") or {}).get("download_url")
            if not download_url:
                raise ValueError(f"No Mendeley download URL for {file_name!r}.")
            size, digest = self._integrity(file, file_name)
            target = output_path / file_name
            self._check_destination(target)
            source = target
            if cache is not None and size is not None and digest is not None:
                identity = [
                    payload.get("id", identifier),
                    payload.get("version"),
                    file.get("id", file_name),
                    (file.get("content_details") or {}).get("id"),
                    size,
                    digest,
                ]
                key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
                source = cache / "mendeley" / key / file_name
                self._check_destination(source)
                self._notify_repo_step(
                    "mendeley",
                    f"Verifying cache: {file_name}",
                    len(jobs) + 1,
                    len(files) + 1,
                )
            cached = source != target and self._verified(source, size, digest)
            reused_target = (
                not cached and source != target and self._verified(target, size, digest)
            )
            jobs.append(
                (
                    file,
                    target,
                    source,
                    size,
                    digest,
                    download_url,
                    cached,
                    reused_target,
                )
            )

        staging_bytes = sum(job[3] or 0 for job in jobs if not job[7])
        cache_bytes = sum(
            job[3] or 0 for job in jobs if job[2] != job[1] and not job[6]
        )
        same_volume = (
            cache is not None and cache.stat().st_dev == output_path.stat().st_dev
        )
        self._check_space(
            output_path, staging_bytes + (cache_bytes if same_volume else 0)
        )
        if cache is not None and not same_volume:
            self._check_space(cache, cache_bytes)

        total_steps = len(jobs) + 1
        for step, (
            file,
            target,
            source,
            size,
            digest,
            url,
            cached,
            reused_target,
        ) in enumerate(jobs, 1):
            if cached:
                message = f"Cache hit: {target.name}"
            elif reused_target:
                message = f"Reusing verified download: {target.name}"
            else:
                message = f"Downloading: {target.name}"
                if source != target and source.exists():
                    message = f"Cache verification failed; downloading: {target.name}"
                elif cache is not None and source == target:
                    message += " (cache bypassed: size and SHA-256 required)"
            self._notify_repo_step("mendeley", message, step, total_steps)
            if reused_target:
                source.parent.mkdir(parents=True, exist_ok=True)
                self._stage_cached(target, source, size, digest)
            elif not cached:
                source.parent.mkdir(parents=True, exist_ok=True)
                self._download(file, source, url)
            if source != target and not reused_target:
                self._notify_repo_step(
                    "mendeley", f"Staging cached file: {target.name}", step, total_steps
                )
                self._stage_cached(source, target, size, digest)
            self._notify_file_complete("mendeley", target.name)

        self._notify_repo_step(
            "mendeley", "Extracting and preparing dataset", total_steps, total_steps
        )
        prepared = self.prepare_fetched(output_path)
        self._notify_repo_done("mendeley", True)
        return prepared

    @staticmethod
    def _check_destination(path: Path) -> None:
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError(f"Refusing Mendeley symlink destination: {path}.")

    @staticmethod
    def _check_space(path: Path, required: int) -> None:
        if shutil.disk_usage(path).free < required:
            raise OSError(
                f"Insufficient disk space for Mendeley downloads in {path}: "
                f"need {required} bytes before extraction."
            )

    @staticmethod
    def _integrity(file: dict[str, Any], name: str) -> tuple[int | None, str | None]:
        details = file.get("content_details") or {}
        size = details.get("size", file.get("size"))
        digest = details.get("sha256_hash")
        if size is not None and (
            not isinstance(size, int) or isinstance(size, bool) or size < 0
        ):
            raise ValueError(f"Invalid Mendeley file size for {name!r}.")
        if digest is not None and (
            not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
        ):
            raise ValueError(f"Invalid Mendeley SHA-256 for {name!r}.")
        return size, digest.lower() if digest is not None else None

    @staticmethod
    def _verified(path: Path, size: int | None, digest: str | None) -> bool:
        if size is None or digest is None or not path.is_file():
            return False
        if path.stat().st_size != size:
            return False

        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest() == digest

    def _stage_cached(
        self, source: Path, target: Path, size: int | None, digest: str | None
    ) -> None:
        self._check_destination(source)
        self._check_destination(target)

        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", suffix=".part", delete=False
        ) as stream:
            partial = Path(stream.name)
            try:
                with source.open("rb") as cached:
                    shutil.copyfileobj(cached, stream, length=1024 * 1024)
                stream.close()
                if not self._verified(partial, size, digest):
                    raise ValueError(
                        f"Mendeley staged file integrity mismatch: {target.name!r}."
                    )
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)

    def _check_access(self, response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise PermissionError(
                f"Mendeley denied access (HTTP {response.status_code}). "
                "This resource requires authorization; pass --token or set "
                "NP_MENDELEY_ACCESS_TOKEN to a valid OAuth access token "
                "with dataset access. Client ID/secret alone are not used by npdb."
            )

    def _download(self, file: dict[str, Any], target: Path, url: str) -> None:
        size, digest = self._integrity(file, target.name)
        self._check_destination(target)
        hasher = hashlib.sha256()
        written = 0

        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", suffix=".part", delete=False
        ) as stream:
            partial = Path(stream.name)
            try:
                with httpx.stream(
                    "GET", url, timeout=60, follow_redirects=True
                ) as response:
                    self._check_access(response)
                    response.raise_for_status()
                    self._notify_file_progress("mendeley", target.name, 0, size or 0)
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        stream.write(chunk)
                        hasher.update(chunk)
                        written += len(chunk)
                        self._notify_file_progress(
                            "mendeley", target.name, written, size or 0
                        )

                if size is not None and written != size:
                    raise ValueError(f"Mendeley size mismatch for {target.name!r}.")
                if digest is not None and hasher.hexdigest() != digest.lower():
                    raise ValueError(f"Mendeley SHA-256 mismatch for {target.name!r}.")
                stream.close()
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)

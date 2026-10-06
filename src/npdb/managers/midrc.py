from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import unquote, urlparse

import httpx

from npdb.managers.model import ProviderManager, ProviderName


class MIDRCProviderManager(ProviderManager):
    provider_name: ClassVar[ProviderName] = ProviderName.MIDRC
    access_type = "restricted"

    def __init__(
        self,
        credentials_path: str | None = None,
        endpoint: str = "https://data.midrc.org",
        cache_dir: str | Path | None = None,
        **_: Any,
    ):
        super().__init__(cache_dir=cache_dir)
        self.credentials_path = credentials_path or os.environ.get(
            "NP_MIDRC_CREDENTIALS"
        )
        self.endpoint = endpoint or os.environ.get(
            "NP_MIDRC_ENDPOINT", "https://data.midrc.org"
        )
        self.endpoint = self.endpoint.rstrip("/")
        parsed = urlparse(self.endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.hostname == "data.neuro.polymtl.ca"
        ):
            raise ValueError("MIDRC endpoint must be a permitted HTTPS URL.")

    def discovery_id(self, identifier: str) -> str | None:
        value = identifier.strip()
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"}:
            endpoint = urlparse(self.endpoint)
            if (
                parsed.scheme != endpoint.scheme
                or parsed.netloc != endpoint.netloc
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "Discovery URL must belong to the configured MIDRC endpoint."
                )
            prefix = endpoint.path.rstrip("/") + "/discovery/"
            if not parsed.path.startswith(prefix):
                raise ValueError("Expected a MIDRC /discovery/<dataset-id> URL.")
            value = unquote(parsed.path[len(prefix) :].rstrip("/"))
            if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                raise ValueError("Invalid MIDRC Discovery dataset ID.")
            return value
        if Path(value).is_file():
            return None
        if re.fullmatch(r"[A-Za-z0-9]+-[A-Za-z0-9]+", value):
            return value
        return None

    def describe(self, identifier: str) -> tuple[str, str]:
        dataset = self.discovery_id(identifier)
        url = f"{self.endpoint}/discovery/{dataset}/" if dataset else self.endpoint
        return url, self.access_type

    def unpack(
        self, fetched: str | Path, *, output_dir: str | Path | None = None
    ) -> Path:
        from npdb.managers.midrc_bids import unpack_midrc

        source = Path(fetched)
        archives = list(source.rglob("*_structured.zip")) if source.is_dir() else []
        if source.name.endswith("_structured.zip"):
            archives = [source]
        if not archives:
            return super().unpack(source)
        destination = Path(output_dir) if output_dir is not None else source
        if not source.is_dir() and output_dir is None:
            raise ValueError("Pass output_dir to unpack a local MIDRC archive.")
        if self.cache_dir and destination.resolve().is_relative_to(
            self.cache_dir.resolve()
        ):
            raise ValueError(
                "MIDRC preparation output must be separate from the cache."
            )
        return unpack_midrc(source.parent if source.is_file() else source, destination)

    def prepare(self, root: str | Path) -> Path:
        from npdb.managers.midrc_bids import (
            prepare_derivatives,
            prepare_imaging,
            prepare_metadata,
        )

        root = Path(root)
        staging = root / "sourcedata" / "midrc"
        if not staging.exists():
            return super().prepare(root)
        manifest = json.loads((staging / "archives.json").read_text(encoding="utf-8"))
        output = self._file_path(root, manifest["Name"])
        if output.exists():
            report_path = self._file_path(staging, "derivative_report.json")
            if not report_path.is_file():
                raise FileExistsError(
                    f"MIDRC preparation is incomplete; use a fresh staging destination: {output}"
                )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not isinstance(report.get("images"), dict) or not isinstance(
                report.get("derivatives"), dict
            ):
                raise ValueError(f"Invalid MIDRC preparation report: {report_path}")
            images = [image for paths in report["images"].values() for image in paths]
            images.extend(report["derivatives"].values())
            required = {
                "dataset_description.json",
                "participants.tsv",
                "participants.json",
            }
            for image in images:
                if not isinstance(image, str) or not image.endswith(
                    (".nii", ".nii.gz")
                ):
                    raise ValueError(f"Invalid MIDRC image path in {report_path}")
                required.add(image)
                required.add(image.removesuffix(".gz").removesuffix(".nii") + ".json")
                if image.startswith("derivatives/"):
                    required.add(
                        str(Path(image).parents[-3] / "dataset_description.json")
                    )
            for relative in sorted(required):
                path = self._file_path(output, relative)
                if not path.is_file() or path.stat().st_size == 0:
                    raise FileNotFoundError(
                        f"Prepared MIDRC dataset is incomplete: {path}"
                    )
            return output
        tables = prepare_metadata(staging / "structured", output, manifest["Name"])
        report = prepare_imaging(staging, output, tables)
        prepare_derivatives(staging, output, tables, report)
        return output

    @staticmethod
    def _manifest_guids(entries: Any) -> list[str]:
        if isinstance(entries, dict):
            entries = entries.get("records") or entries.get("files")
        if not isinstance(entries, list) or not entries:
            raise ValueError(
                "MIDRC manifest must contain a non-empty list of file entries."
            )
        guids: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("MIDRC manifest entries must be JSON objects.")
            guid = entry.get("object_id") or entry.get("guid") or entry.get("did")
            if not isinstance(guid, str) or not guid.strip():
                raise ValueError(
                    "Each MIDRC file entry requires object_id, guid, or did."
                )
            guid = guid.strip()
            if (
                urlparse(guid).scheme
                or "\\" in guid
                or any(part in {"", ".", ".."} for part in guid.split("/"))
                or not re.fullmatch(r"[A-Za-z0-9._/-]+", guid)
            ):
                raise ValueError(f"Invalid MIDRC file GUID: {guid!r}.")
            if guid not in guids:
                guids.append(guid)
        return guids

    def _resolve(self, identifier: str) -> list[str]:
        manifest = Path(identifier)
        if manifest.is_file():
            with manifest.open(encoding="utf-8") as stream:
                return self._manifest_guids(json.load(stream))
        if manifest.exists():
            raise ValueError(
                f"MIDRC manifest must be a regular JSON file: {identifier}"
            )
        dataset = self.discovery_id(identifier)
        if dataset:
            self.ensure_cache_dir(required=True)
            response = httpx.get(f"{self.endpoint}/mds/metadata/{dataset}", timeout=30)
            response.raise_for_status()
            payload = response.json()
            metadata = (
                payload.get("gen3_discovery") if isinstance(payload, dict) else None
            )
            if not isinstance(metadata, dict):
                raise ValueError(
                    "MIDRC Discovery response has no gen3_discovery metadata."
                )
            links = metadata.get("data_download_links")
            if not isinstance(links, list) or not links:
                raise ValueError(
                    "MIDRC dataset has no downloadable file links. "
                    "Export a file manifest from Exploration instead."
                )
            return self._manifest_guids(links)
        if manifest.suffix.lower() == ".json" or identifier.startswith(
            ("/", "./", "../")
        ):
            raise FileNotFoundError(f"MIDRC manifest not found: {identifier}")
        return self._manifest_guids([{"guid": identifier}])

    @staticmethod
    def _file_path(root: Path, name: str) -> Path:
        relative = Path(name)
        if (
            not name
            or relative.is_absolute()
            or "\\" in name
            or ":" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            raise ValueError(f"Unsafe MIDRC filename: {name!r}.")
        target = root / relative
        if any(path.is_symlink() for path in (target, *target.parents)):
            raise ValueError(f"Refusing MIDRC symlink destination: {target}.")
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Unsafe MIDRC filename: {name!r}.")
        return target

    @staticmethod
    def _integrity(record: dict[str, Any]) -> tuple[int, str | None, str | None]:
        size = record.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("MIDRC index record requires a non-negative file size.")
        hashes = record.get("hashes") or {}
        if not isinstance(hashes, dict):
            raise ValueError("Invalid MIDRC index hashes.")
        for algorithm, length in (("sha256", 64), ("sha1", 40), ("md5", 32)):
            digest = hashes.get(algorithm)
            if digest is not None:
                if not isinstance(digest, str) or not re.fullmatch(
                    rf"[0-9a-fA-F]{{{length}}}", digest
                ):
                    raise ValueError(f"Invalid MIDRC {algorithm} checksum.")
                return size, algorithm, digest.lower()
        return size, None, None

    @staticmethod
    def _verified(
        path: Path,
        size: int,
        algorithm: str | None,
        digest: str | None,
    ) -> bool:
        if not path.is_file() or path.stat().st_size != size:
            return False
        if algorithm:
            hasher = hashlib.new(algorithm)
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
            return hasher.hexdigest() == digest
        return True

    def _download(
        self,
        files: Any,
        guid: str,
        target: Path,
        size: int,
        algorithm: str | None,
        digest: str | None,
    ) -> None:
        from requests.exceptions import HTTPError

        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        if partial.exists() or partial.is_symlink():
            raise FileExistsError(
                f"Remove the incomplete MIDRC file before retrying: {partial}"
            )
        try:
            for attempt in range(2):
                try:
                    signed = files.get_presigned_url(guid)
                except HTTPError as exc:
                    status = (
                        exc.response.status_code
                        if exc.response is not None
                        else "unknown"
                    )
                    raise RuntimeError(
                        f"MIDRC authorization failed for {guid} (HTTP {status}). "
                        "Check your credentials and dataset access."
                    ) from None
                url = signed.get("url") if isinstance(signed, dict) else None
                if not isinstance(url, str):
                    raise ValueError(f"MIDRC returned no signed URL for {guid}.")
                parsed = urlparse(url)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.hostname == "data.neuro.polymtl.ca"
                ):
                    raise ValueError(
                        f"MIDRC returned no valid signed HTTPS URL for {guid}."
                    )
                try:
                    with httpx.stream("GET", url, timeout=60) as response:
                        if response.status_code == 403 and attempt == 0:
                            error = b""
                            for chunk in response.iter_bytes(chunk_size=4096):
                                error += chunk
                                if len(error) >= 8192:
                                    break
                            if (
                                b"<Code>ExpiredToken</Code>" in error
                                or b"<Code>RequestExpired</Code>" in error
                            ):
                                continue
                        response.raise_for_status()
                        with partial.open("xb") as stream:
                            done = 0
                            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                                done += len(chunk)
                                if done > size:
                                    raise ValueError(
                                        f"MIDRC download exceeds indexed size for {guid}."
                                    )
                                stream.write(chunk)
                                self._notify_file_progress("midrc", guid, done, size)
                    break
                except httpx.HTTPError:
                    raise RuntimeError(
                        f"MIDRC storage download failed for {guid}; retry the command."
                    ) from None
            if not self._verified(partial, size, algorithm, digest):
                raise ValueError(f"MIDRC download size/checksum mismatch for {guid}.")
            partial.replace(target)
            self._notify_file_complete("midrc", guid)
        finally:
            if partial.is_file() and not partial.is_symlink():
                partial.unlink()

    def fetch(self, identifier: str, output_dir: str | Path, **_: Any) -> Path:
        if not self.credentials_path:
            raise ValueError(
                "MIDRC requires a credentials file. Set NP_MIDRC_CREDENTIALS or pass credentials_path. "
                "The file is usually downloaded from your MIDRC profile as credentials.json."
            )
        guids = self._resolve(identifier)
        try:
            from gen3.auth import Gen3Auth
            from gen3.file import Gen3File
            from gen3.index import Gen3Index
        except ImportError as exc:  # pragma: no cover - environment guard
            raise ImportError(
                "The MIDRC provider requires: uv sync --group midrc"
            ) from exc
        auth = Gen3Auth(self.endpoint, self.credentials_path)
        index = Gen3Index(self.endpoint, auth)
        files = Gen3File(self.endpoint, auth)
        output_path = Path(output_dir)
        if any(path.is_symlink() for path in (output_path, *output_path.parents)):
            raise ValueError("MIDRC output directory must not contain symlinks.")
        output_path.mkdir(parents=True, exist_ok=True)
        cache = self.ensure_cache_dir()
        if cache:
            if cache.resolve().is_relative_to(
                output_path.resolve()
            ) or output_path.resolve().is_relative_to((cache / "midrc").resolve()):
                raise ValueError("MIDRC cache and fetched directory must not overlap.")
            if any(path.is_symlink() for path in (cache, *cache.parents)):
                raise ValueError("MIDRC cache directory must not contain symlinks.")
            cache.mkdir(parents=True, exist_ok=True)
        jobs = []
        names: set[Path] = set()
        for guid in guids:
            item = index.get_record(guid)
            if not isinstance(item, dict):
                raise ValueError(f"No MIDRC index record found for {guid}.")
            name = item.get("file_name") or guid.replace("/", "_")
            if not isinstance(name, str):
                raise ValueError(f"Invalid MIDRC filename for {guid}.")
            target = self._file_path(output_path, name)
            if any(
                target == previous
                or target in previous.parents
                or previous in target.parents
                for previous in names
            ):
                raise FileExistsError(f"Conflicting MIDRC filenames: {name}.")
            names.add(target)
            size, algorithm, digest = self._integrity(item)
            source = target
            if cache:
                key = hashlib.sha256(f"{self.endpoint}/{guid}".encode()).hexdigest()
                source = self._file_path(cache / "midrc" / key, name)
                if source.resolve() == target.resolve():
                    raise ValueError(
                        "MIDRC cache must be separate from the fetched directory."
                    )
            staged = self._verified(target, size, algorithm, digest)
            if target.exists() and not staged:
                raise FileExistsError(
                    f"MIDRC destination already exists with different content: {target}."
                )
            cached = staged or (
                source != target and self._verified(source, size, algorithm, digest)
            )
            jobs.append((guid, target, source, size, algorithm, digest, staged, cached))
        staging_bytes = sum(job[3] for job in jobs if not job[6])
        cache_bytes = sum(job[3] for job in jobs if not job[7] and cache)
        same_volume = (
            cache is not None and cache.stat().st_dev == output_path.stat().st_dev
        )
        required = staging_bytes + (cache_bytes if same_volume else 0)
        if shutil.disk_usage(output_path).free < required:
            raise OSError(
                f"Insufficient disk space for MIDRC staging: need {required} "
                "bytes before extraction."
            )
        if cache and not same_volume and shutil.disk_usage(cache).free < cache_bytes:
            raise OSError(
                f"Insufficient disk space for MIDRC cache: need {cache_bytes} bytes."
            )
        total_steps = len(jobs) + 1
        download_step = (
            f"Downloading {len(jobs)} file(s), {sum(job[3] for job in jobs)} bytes"
        )
        for completed, (
            guid,
            target,
            source,
            size,
            algorithm,
            digest,
            staged,
            cached,
        ) in enumerate(jobs):
            self._notify_repo_step("midrc", download_step, completed, total_steps)
            if staged:
                self._notify_file_complete("midrc", guid)
                continue
            if not cached:
                self._download(files, guid, source, size, algorithm, digest)
            if source != target:
                target.parent.mkdir(parents=True, exist_ok=True)
                partial = target.with_name(target.name + ".part")
                if partial.exists() or partial.is_symlink():
                    raise FileExistsError(
                        f"Remove the incomplete MIDRC staging file: {partial}"
                    )
                try:
                    with partial.open("xb") as stream, source.open("rb") as cached:
                        shutil.copyfileobj(cached, stream, length=1024 * 1024)
                    if not self._verified(partial, size, algorithm, digest):
                        raise ValueError(
                            f"MIDRC staged file size/checksum mismatch for {guid}."
                        )
                    partial.replace(target)
                finally:
                    if partial.is_file() and not partial.is_symlink():
                        partial.unlink()
            if cached:
                self._notify_file_complete("midrc", guid)
        self._notify_repo_step("midrc", "Preparing dataset", len(jobs), total_steps)
        prepared = self.prepare_fetched(output_path)
        self._notify_repo_step("midrc", "Dataset prepared", total_steps, total_steps)
        self._notify_repo_done("midrc", True)
        return prepared

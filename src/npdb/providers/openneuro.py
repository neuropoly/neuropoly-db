from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from npdb.extras import require_module
from npdb.providers.model import Provider, ProviderName

_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    reraise=True,
)

_SNAPSHOT_FILES_QUERY = """
query ($datasetId: ID!, $tag: String!) {
  snapshot(datasetId: $datasetId, tag: $tag) {
    files(recursive: true) { filename directory annexed }
  }
}
"""
_LATEST_TAG_QUERY = """
query ($datasetId: ID!) {
  dataset(id: $datasetId) { latestSnapshot { tag } }
}
"""


class OpenNeuroProvider(Provider):
    provider_name: ClassVar[ProviderName] = ProviderName.OPENNEURO

    GRAPHQL_URL = "https://openneuro.org/crn/graphql"
    # Unfetched annexed files point here; the directory is never created.
    PLACEHOLDER_TARGET = ".openneuro-unfetched"

    def fetch(
        self,
        identifier: str,
        output_dir: str | Path,
        *,
        metadata_only: bool = True,
        tag: str | None = None,
        **kwargs: Any,
    ) -> Path:
        openneuro = require_module(
            "openneuro", extra="openneuro", feature="The OpenNeuro provider"
        )

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        if not metadata_only:
            openneuro.download(
                dataset=identifier, tag=tag, target_dir=str(output_path), **kwargs
            )
            return self.prepare_fetched(output_path)

        tag = tag or self.latest_tag(identifier)
        metadata, annexed = self.snapshot_files(identifier, tag)
        openneuro.download(
            dataset=identifier,
            tag=tag,
            target_dir=str(output_path),
            include=[f"/{_escape_glob(name)}" for name in metadata],
            **kwargs,
        )
        self.link_placeholders(output_path, annexed)

        return self.prepare_fetched(output_path)

    def latest_tag(self, identifier: str) -> str:
        data = self._graphql(_LATEST_TAG_QUERY, {"datasetId": identifier})
        snapshot = (data.get("dataset") or {}).get("latestSnapshot") or {}
        if not snapshot.get("tag"):
            raise ValueError(f"OpenNeuro dataset '{identifier}' has no snapshot.")
        return snapshot["tag"]

    def snapshot_files(self, identifier: str, tag: str) -> tuple[list[str], list[str]]:
        """Split a snapshot's files into (git-tracked metadata, annexed data)."""
        data = self._graphql(
            _SNAPSHOT_FILES_QUERY, {"datasetId": identifier, "tag": tag}
        )
        snapshot = data.get("snapshot")
        if not snapshot:
            raise ValueError(f"OpenNeuro snapshot '{identifier}:{tag}' was not found.")

        metadata: list[str] = []
        annexed: list[str] = []
        for entry in snapshot.get("files") or []:
            if entry.get("directory"):
                continue
            name = _safe_relative(entry["filename"])
            if any(part.startswith(".") for part in PurePosixPath(name).parts):
                if name != ".bidsignore":
                    continue
            (annexed if entry.get("annexed") else metadata).append(name)
        return metadata, annexed

    def link_placeholders(self, root: Path, files: list[str]) -> None:
        """Expose unfetched data files as dangling symlinks, like git-annex."""
        for name in files:
            link = root / name
            if link.exists() or link.is_symlink():
                continue

            link.parent.mkdir(parents=True, exist_ok=True)
            target = root / self.PLACEHOLDER_TARGET / name
            os.symlink(os.path.relpath(target, link.parent), link)

    @_RETRY
    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            self.GRAPHQL_URL,
            json={"query": query, "variables": variables},
            timeout=60,
        )
        response.raise_for_status()

        payload = response.json()
        if payload.get("errors"):
            messages = "; ".join(e.get("message", "") for e in payload["errors"])
            raise RuntimeError(f"OpenNeuro metadata query failed: {messages}")
        return payload.get("data") or {}


def _safe_relative(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or not path.parts:
        raise ValueError(f"Unsafe OpenNeuro file path: {name!r}")
    return path.as_posix()


def _escape_glob(name: str) -> str:
    return "".join(f"[{c}]" if c in "*?[" else c for c in name)

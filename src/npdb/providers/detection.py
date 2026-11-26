"""
Automatic provider selection for 'npdb download'.

To make 'npdb download' support new provider, add one ProviderRoute to ROUTES
* patterns : regexes recognizing the provider's URLs, with a named group "id"
* fetch_kwargs : how to ask that provider<s fetch() for only the given BIDS paths.

"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping

from npdb.providers.model import ProviderName

URL_COLUMNS = ("RepositoryURL", "AccessLink")


@dataclass(frozen=True)
class DatasetMatch:
    provider: ProviderName
    identifier: str
    version: str | None = None

    @property
    def folder_name(self) -> str:
        return self.identifier.replace("/", "_")


@dataclass(frozen=True)
class ProviderRoute:
    provider: ProviderName
    patterns: tuple[re.Pattern[str], ...]
    fetch_kwargs: Callable[[list[str], str | None], dict[str, Any]]

    def match(self, url: str) -> tuple[str, str | None] | None:
        for pattern in self.patterns:
            found = pattern.search(url)
            if found:
                groups = found.groupdict()
                return groups["id"], groups.get("version")
        return None


def _openneuro_fetch_kwargs(paths: list[str], version: str | None) -> dict[str, Any]:
    return {"metadata_only": False, "include": paths, "tag": version}


ROUTES: tuple[ProviderRoute, ...] = (
    ProviderRoute(
        provider=ProviderName.OPENNEURO,
        patterns=(
            re.compile(
                r"openneuro\.org/datasets/(?P<id>ds\d{6})(?:/versions/(?P<version>[^/?#]+))?",
                re.IGNORECASE,
            ),
            re.compile(
                r"github\.com/OpenNeuroDatasets(?:-JSONLD)?/(?P<id>ds\d{6})",
                re.IGNORECASE,
            ),
        ),
        fetch_kwargs=_openneuro_fetch_kwargs,
    ),
)


def detect_provider(
    row: Mapping[str, str], routes: tuple[ProviderRoute, ...] = ROUTES
) -> DatasetMatch | None:
    """Return the provider, identifier and version a query-result row points to, if any."""
    for column in URL_COLUMNS:
        url = (row.get(column) or "").strip()
        if not url:
            continue
        for route in routes:
            found = route.match(url)
            if found:
                identifier, version = found
                return DatasetMatch(route.provider, identifier, version)
    return None


def route_for(
    provider: ProviderName, routes: tuple[ProviderRoute, ...] = ROUTES
) -> ProviderRoute:
    for route in routes:
        if route.provider == provider:
            return route
    raise KeyError(f"No download route registered for provider '{provider}'.")


def bids_relative_path(imaging_path: str) -> str | None:
    """'/ds006036/sub-001' -> 'sub-001'; '' or 'protected' -> None."""
    parts = PurePosixPath(imaging_path).parts
    for index, part in enumerate(parts):
        if part.startswith("sub-"):
            return "/".join(parts[index:])
    return None

"""Resolve explicitly requested BIDS versions without guessing offline defaults."""

import re

import httpx

LATEST_RELEASE_URL = (
    "https://api.github.com/repos/bids-standard/bids-specification/releases/latest"
)
_RELEASE_VERSION = re.compile(r"^v?([0-9]+\.[0-9]+\.[0-9]+)$")


def resolve_bids_version(version: str | None) -> str | None:
    if version is None:
        return None
    if not isinstance(version, str) or not version.strip():
        raise ValueError("bids_version must be a non-empty version string.")

    version = version.strip()
    if version.lower() != "latest":
        return version

    try:
        response = httpx.get(
            LATEST_RELEASE_URL,
            headers={"Accept": "application/vnd.github+json"},
            timeout=30,
            follow_redirects=False,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(
            "Cannot resolve the latest stable BIDS release. Check network access "
            "or supply an explicit --bids-version VERSION."
        ) from exc

    try:
        release = response.json()
    except ValueError as exc:
        raise RuntimeError("BIDS release service returned invalid JSON.") from exc
    if not isinstance(release, dict):
        raise RuntimeError("BIDS release service returned an invalid release object.")

    tag = release.get("tag_name")
    match = _RELEASE_VERSION.fullmatch(tag) if isinstance(tag, str) else None
    if (
        not match
        or release.get("draft") is not False
        or release.get("prerelease") is not False
    ):
        raise RuntimeError(
            "BIDS release service did not identify a stable semantic version; "
            "supply an explicit --bids-version VERSION."
        )

    return match.group(1)

"""
Static phenotype mappings and resolver for annotation automation.

Provides loader and precedence-based resolver for mapping column headers to
Neurobagel standardized variables.
"""

import copy
import json
from pathlib import Path
from typing import Any

_DEFAULT_RESOURCE_PATH = (
    Path(__file__).parent.parent.parent / "resources" / "phenotype_mappings.json"
)

# Module-level cache: avoid re-reading the same JSON file on every instantiation.
_static_mappings_cache: dict[str, Any] | None = None


def load_static_mappings(resource_path: Path | None = None) -> dict[str, Any]:
    """
    Load built-in static phenotype mappings.

    The parsed JSON is cached after the first successful load from the default
    path.  A deep copy is returned so callers cannot mutate the cached value.
    Passing an explicit *resource_path* bypasses the cache so that callers can
    override the bundled file in tests.

    Args:
        resource_path: Optional override path; defaults to bundled phenotype_mappings.json

    Returns:
        Dictionary of mappings with context and column definitions.
    """
    global _static_mappings_cache

    use_default = resource_path is None
    if use_default and _static_mappings_cache is not None:
        return copy.deepcopy(_static_mappings_cache)

    path = _DEFAULT_RESOURCE_PATH if use_default else resource_path
    if not path.exists():
        raise FileNotFoundError(f"Phenotype mappings file not found: {path}")

    with open(path, "r") as f:
        data = json.load(f)

    if use_default:
        _static_mappings_cache = data

    return copy.deepcopy(data)


def merge_mappings(
    builtin: dict[str, Any], user_mappings: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Merge user-supplied mappings with built-in mappings.

    User mappings take precedence over built-in mappings.

    Args:
        builtin: Built-in mappings registry
        user_mappings: Optional user-supplied mappings (same schema as builtin)

    Returns:
        Merged mappings dictionary with user overrides applied.
    """
    merged = builtin.copy()

    if user_mappings:
        # Merge @context
        if "@context" in user_mappings:
            merged.setdefault("@context", {}).update(user_mappings["@context"])

        # Merge mappings (user overrides builtin)
        if "mappings" in user_mappings:
            merged.setdefault("mappings", {}).update(user_mappings["mappings"])

    return merged


def load_user_mappings(path: str | Path) -> dict[str, Any]:
    """
    Load user-supplied phenotype mappings from JSON file.

    Args:
        path: Path (str or Path object) to user mapping JSON file

    Returns:
        User mappings dictionary

    Raises:
        FileNotFoundError: If file does not exist
        json.JSONDecodeError: If file is invalid JSON
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"User mappings file not found: {path}")

    with open(path, "r") as f:
        data = json.load(f)

    return data

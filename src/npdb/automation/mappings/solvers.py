"""
Static phenotype mappings and resolver for annotation automation.

Provides loader and precedence-based resolver for mapping column headers to
Neurobagel standardized variables.
"""

import json
from pathlib import Path
from typing import Dict, Optional, Any


def load_static_mappings(resource_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Load built-in static phenotype mappings.

    Args:
        resource_path: Optional override path; defaults to bundled phenotype_mappings.json

    Returns:
        Dictionary of mappings with context and column definitions.
    """
    if resource_path is None:
        resource_path = Path(__file__).parent.parent.parent / \
            "resources" / "phenotype_mappings.json"

    if not resource_path.exists():
        raise FileNotFoundError(
            f"Phenotype mappings file not found: {resource_path}")

    with open(resource_path, "r") as f:
        data = json.load(f)

    return data


def merge_mappings(
    builtin: Dict[str, Any],
    user_mappings: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
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


def load_user_mappings(path: str | Path) -> Dict[str, Any]:
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
    path = Path(path) if isinstance(path, str) else path

    if not path.exists():
        raise FileNotFoundError(f"User mappings file not found: {path}")

    with open(path, "r") as f:
        data = json.load(f)

    return data

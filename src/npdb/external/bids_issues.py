"""Classify validator evidence without rewriting original upstream issue codes."""

from pathlib import Path
from typing import Any


def repair_kind(issue: dict[str, Any]) -> str | None:
    code = issue.get("code")
    if code == "INVALID_DATASET_DESCRIPTION":
        return "description"
    if code in {
        "MISSING_DATASET_DESCRIPTION", "JSON_INVALID", "JSON_NOT_AN_OBJECT",
        "INVALID_JSON_ENCODING", "JSON_KEY_REQUIRED", "JSON_SCHEMA_VALIDATION_ERROR",
    } and Path(str(issue.get("path", ""))).name == "dataset_description.json":
        return "description"
    if code in {
        "INVALID_FILENAME", "FILENAME_MISMATCH", "INVALID_ENTITY_LABEL",
        "ENTITY_WITH_NO_LABEL", "MISSING_REQUIRED_ENTITY", "ENTITY_NOT_IN_RULE",
        "ALL_FILENAME_RULES_HAVE_ISSUES", "NOT_INCLUDED",
    }:
        return "filename"
    return None

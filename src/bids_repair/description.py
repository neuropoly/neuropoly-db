"""Dataset-description inspection and metadata editing without repository policy."""

import json
from pathlib import Path
from typing import Any

from bids_repair.paths import assert_safe_path


def inspect_dataset_description(owner: Path) -> dict[str, Any]:
    """Explain JSON syntax and common schema failures without inventing metadata."""
    path = owner / "dataset_description.json"
    diagnostics: dict[str, Any] = {
        "path": str(path),
        "json_valid": False,
        "problems": [],
    }
    if path.is_symlink() or not path.is_file():
        diagnostics["problems"].append("dataset_description.json is missing or unsafe.")
        return diagnostics

    try:
        document = json.loads(path.read_bytes())
    except json.JSONDecodeError as exc:
        diagnostics["problems"].append(
            f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}."
        )
        return diagnostics
    except (OSError, UnicodeDecodeError) as exc:
        diagnostics["problems"].append(f"Cannot read JSON: {exc}.")
        return diagnostics

    diagnostics["json_valid"] = True
    problems = diagnostics["problems"]
    if not isinstance(document, dict):
        problems.append("The JSON root must be an object.")
        return diagnostics

    for field in ("Name", "BIDSVersion"):
        if field not in document:
            problems.append(f"{field} is missing (required field).")
        elif not isinstance(document[field], str) or not document[field].strip():
            problems.append(f"{field} must be a non-empty string.")
    for field in ("License", "Acknowledgements", "HowToAcknowledge", "DatasetDOI"):
        if field in document and not isinstance(document[field], str):
            problems.append(f"{field} must be a string.")
    for field in ("Authors", "Funding", "EthicsApprovals", "ReferencesAndLinks"):
        if field in document and not (
            isinstance(document[field], list)
            and all(isinstance(item, str) for item in document[field])
        ):
            problems.append(f"{field} must be an array of strings.")
    if "DatasetType" in document and document["DatasetType"] not in (
        "raw",
        "derivative",
        "study",
    ):
        problems.append("DatasetType must be raw, derivative, or study.")
    if document.get("DatasetType") == "derivative" and "GeneratedBy" not in document:
        problems.append("GeneratedBy is required for derivative datasets.")
    if "GeneratedBy" in document:
        generators = document["GeneratedBy"]
        if not isinstance(generators, list):
            problems.append("GeneratedBy must be an array of objects.")
        else:
            for index, generator in enumerate(generators):
                if not isinstance(generator, dict):
                    problems.append(f"GeneratedBy[{index}] must be an object.")
                elif (
                    not isinstance(generator.get("Name"), str)
                    or not generator["Name"].strip()
                ):
                    problems.append(
                        f"GeneratedBy[{index}].Name must be a non-empty string."
                    )

    diagnostics["scope"] = (
        "JSON syntax and common field checks; not full BIDS compliance."
    )
    return diagnostics


def read_dataset_description(root: Path) -> dict[str, Any]:
    root = root.resolve()
    path = root / "dataset_description.json"
    assert_safe_path(root, path, must_exist=True)

    description = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(description, dict):
        raise ValueError("dataset_description.json must contain an object")
    return description


def update_dataset_description(root: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    description = read_dataset_description(root)
    description.update(metadata)

    path = root.resolve() / "dataset_description.json"
    path.write_text(json.dumps(description, indent=2) + "\n", encoding="utf-8")
    return description

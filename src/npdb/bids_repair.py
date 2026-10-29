"""Evidence-based, recoverable repairs for structural BIDS validation findings."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

from bids.layout import Config

from npdb.external.bids_version import resolve_bids_version


class BidsRepairError(RuntimeError):
    """A repair plan could not be safely applied or recovered."""


_ENTITY_PREFIX_TO_NAME = {
    "sub": "subject",
    "ses": "session",
    "sample": "sample",
    "task": "task",
    "tracksys": "tracksys",
    "acq": "acquisition",
    "nuc": "nucleus",
    "voi": "volume",
    "ce": "ceagent",
    "stain": "staining",
    "trc": "tracer",
    "rec": "reconstruction",
    "dir": "direction",
    "run": "run",
    "proc": "proc",
    "mod": "modality",
    "echo": "echo",
    "flip": "flip",
    "inv": "inv",
    "mt": "mt",
    "part": "part",
    "recording": "recording",
    "space": "space",
    "chunk": "chunk",
    "split": "split",
    "res": "resolution",
    "den": "density",
    "hemi": "hemisphere",
    "desc": "description",
    "atlas": "atlas",
    "label": "label",
    "from": "from",
    "to": "to",
    "mode": "mode",
}
_EXTENSIONS = (
    ".nii.gz",
    ".tsv.gz",
    ".json",
    ".nii",
    ".tsv",
    ".bval",
    ".bvec",
    ".edf",
    ".set",
    ".fdt",
    ".vhdr",
    ".eeg",
    ".vmrk",
    ".snirf",
    ".nwb",
    ".mat",
    ".csv",
)
_IMAGE_EXTENSIONS = {
    ".nii",
    ".nii.gz",
    ".edf",
    ".set",
    ".vhdr",
    ".eeg",
    ".snirf",
    ".nwb",
}
_ENTITY_TOKEN = re.compile(r"^([A-Za-z][A-Za-z0-9]*)-(.+)$")
_RUN_WITHOUT_SEPARATOR = re.compile(r"^run([0-9]+)$", re.IGNORECASE)
_DIRECTORY_ENTITY = re.compile(r"^(sub|ses)-([A-Za-z0-9+]+)$")
_HASH_SIZE = 1024 * 1024


def plan_bids_repair(
    dataset: str | Path,
    *,
    bids_version: str | None = None,
    suffix_aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a deterministic JSON-serializable plan without writing dataset files."""
    from npdb.external.bids import get_bids_validation_report

    root = _validate_dataset_root(dataset)
    bids_version = resolve_bids_version(bids_version)
    aliases = _validate_aliases(
        suffix_aliases if suffix_aliases is not None else {}
    )
    report = get_bids_validation_report(root)
    return _plan_from_report(root, report, bids_version=bids_version, suffix_aliases=aliases)


def _plan_from_report(
    root: Path,
    report: dict[str, Any],
    *,
    bids_version: str | None,
    suffix_aliases: dict[str, str],
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    rename_map: dict[str, str] = {}
    owner_roots: dict[str, str] = {}
    rename_source_findings: dict[str, list[dict[str, Any]]] = {}
    blocked_rename_sources: set[str] = set()
    issue_records = list(_iter_issues(report))

    for issue, owner in issue_records:
        owner = Path(os.path.abspath(owner))
        finding = _normalise_finding(issue, owner)
        findings.append(finding)
        if not _is_within(root, owner):
            finding["resolution"] = "The validator reported a dataset root outside the requested dataset."
            continue
        if _contains_symlink(root, owner):
            finding["resolution"] = "The validator-reported dataset path contains a symlink."
            continue
        if finding["code"] == "INVALID_DATASET_DESCRIPTION":
            finding["diagnostics"] = inspect_dataset_description(owner)
            operation, explanation = _description_repair(owner, bids_version)
            if operation:
                operation["original_finding"] = dict(issue)
                operations.append(operation)
                finding.update(
                    action="normalize_dataset_description",
                    resolution="planned",
                    message=f"{finding['message']}; {explanation}",
                )
            else:
                finding.update(resolution=explanation)
            if "BIDSVersion is missing" in explanation or "BIDSVersion has an invalid type" in explanation:
                version_message = (
                    "BIDSVersion is missing; provide an explicit version with --bids-version."
                    if "BIDSVersion is missing" in explanation
                    else "BIDSVersion has an invalid type; provide an explicit version with --bids-version."
                )
                findings.append(
                    _finding(
                        "BIDS_VERSION_REQUIRED",
                        "error",
                        version_message,
                        dataset=str(owner),
                        path=str(owner / "dataset_description.json"),
                        resolution="The repair engine never guesses a BIDS version.",
                        required_input="--bids-version VERSION",
                    )
                )
            if "invalid JSON" in explanation:
                findings.append(
                    _finding(
                        "INVALID_DATASET_DESCRIPTION_JSON",
                        "error",
                        "dataset_description.json contains malformed JSON.",
                        dataset=str(owner),
                        path=str(owner / "dataset_description.json"),
                        resolution="Malformed JSON is reported but never rewritten speculatively.",
                    )
                )
            if any(
                message in explanation
                for message in (
                    "must contain a JSON object",
                    "unsupported type",
                    "invalid type",
                )
            ):
                findings.append(
                    _finding(
                        "INVALID_DATASET_DESCRIPTION_SCHEMA",
                        "error",
                        explanation,
                        dataset=str(owner),
                        path=str(owner / "dataset_description.json"),
                        resolution="The invalid field requires user correction.",
                    )
                )
        elif finding["code"] == "INVALID_FILENAME":
            source = _issue_file_path(owner, finding.get("path"))
            target, reason = _normalized_filename(
                source, owner, suffix_aliases
            ) if source else (None, "The validator finding has no safe dataset-relative file path.")
            if source is None:
                finding["resolution"] = reason
                continue
            if target is None:
                finding["resolution"] = reason
                continue
            group, association_reason = _associated_renames(source, target)
            if association_reason:
                finding["resolution"] = association_reason
                continue
            for original, replacement in group.items():
                original_key = str(original)
                if original_key in blocked_rename_sources:
                    finding["resolution"] = (
                        "Conflicting validator findings propose different names for the same file."
                    )
                    continue
                previous = rename_map.get(original_key)
                if previous and previous != str(replacement):
                    rename_map.pop(original_key, None)
                    owner_roots.pop(original_key, None)
                    rename_source_findings.pop(original_key, None)
                    blocked_rename_sources.add(original_key)
                    finding["resolution"] = (
                        "Conflicting validator findings propose different names for the same file."
                    )
                    break
                rename_map[original_key] = str(replacement)
                owner_roots[original_key] = str(owner)
                rename_source_findings.setdefault(original_key, []).append(dict(issue))
                finding.update(
                    action="rename_file",
                    resolution="planned",
                    proposed_path=str(replacement),
                )

    for source_string, target_string in sorted(rename_map.items()):
        source, target = Path(source_string), Path(target_string)
        try:
            source_hash = _sha256(source)
        except OSError as exc:
            findings.append(
                _finding(
                    "UNSAFE_RENAME_SOURCE",
                    "error",
                    f"Cannot safely inspect rename source: {exc}",
                    path=source_string,
                    resolution="No operation was planned.",
                )
            )
            continue
        operations.append(
            _operation(
                "rename",
                source,
                target,
                expected_sha256=source_hash,
                affected_paths=[str(source), str(target)],
                evidence="Validator reported INVALID_FILENAME; entity values came only from filename syntax or enclosing sub-/ses- directories.",
                diagnostic={
                    "code": "REPAIRED_FILENAME",
                    "severity": "info",
                    "message": f"Renamed {source.name} to {target.name}.",
                    "path": str(source),
                    "proposed_path": str(target),
                    "affected_paths": [str(source), str(target)],
                },
            )
        )
        operations[-1]["original_findings"] = rename_source_findings.get(
            source_string, []
        )

    reference_operations, reference_findings = _intendedfor_updates(
        root, rename_map, owner_roots
    )
    rename_operations = {
        operation["source"]: operation
        for operation in operations
        if operation["kind"] == "rename"
    }
    for reference_operation in reference_operations:
        rename_operation = rename_operations.get(reference_operation["source"])
        if rename_operation is None:
            operations.append(reference_operation)
            continue
        rename_operation["content"] = reference_operation["content"]
        rename_operation["result_sha256"] = hashlib.sha256(
            reference_operation["content"].encode("utf-8")
        ).hexdigest()
        rename_operation.setdefault(
            "diagnostics", [rename_operation.get("diagnostic")]
        ).append(
            reference_operation["diagnostic"]
        )
    findings.extend(reference_findings)

    operations.sort(key=lambda item: (item["kind"], item["source"], item.get("target", "")))
    findings.sort(
        key=lambda item: (
            str(item.get("path", "")),
            str(item["code"]),
            str(item["message"]),
        )
    )
    blocking = any(
        item["severity"] == "error" and item.get("resolution") != "planned"
        for item in findings
    )
    return {
        "dataset": str(root),
        "bids_version": bids_version,
        "suffix_aliases": dict(sorted(suffix_aliases.items())),
        "findings": findings,
        "operations": operations,
        "blocking": blocking,
        "validation_report": report,
    }


def apply_bids_repair(plan: dict[str, Any]) -> dict[str, Any]:
    """Preflight and apply a plan with a persistent rollback journal and metadata backups."""
    root = _validate_dataset_root(plan.get("dataset", ""))
    operations = plan.get("operations")
    if not isinstance(operations, list):
        raise BidsRepairError("Repair plan has no valid operations list.")
    unresolved = [
        item
        for item in plan.get("findings", [])
        if isinstance(item, dict) and item.get("resolution") != "planned"
    ]
    if not operations:
        return {"applied": [], "unresolved": unresolved, "journal": None, "backups": []}

    _preflight_operations(root, operations)
    journal_root = _journal_root(root)
    journal_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if journal_root.is_symlink():
        raise BidsRepairError(f"Repair journal directory is a symlink: {journal_root}")
    _ensure_no_incomplete_journal(journal_root)
    journal_dir = journal_root / uuid.uuid4().hex
    journal_dir.mkdir(mode=0o700)
    backup_dir = journal_dir / "backups"
    backup_dir.mkdir(mode=0o700)
    journal_path = journal_dir / "journal.json"
    entries = []
    backups: list[str] = []

    for index, operation in enumerate(operations):
        entry = {key: operation.get(key) for key in (
            "kind",
            "source",
            "target",
            "expected_sha256",
            "content",
            "diagnostic",
            "diagnostics",
            "result_sha256",
        )}
        entry["state"] = "pending"
        if operation.get("kind") == "write" or operation.get("content") is not None:
            backup = backup_dir / f"{index:06d}.original"
            shutil.copyfile(operation["source"], backup)
            entry["backup"] = str(backup)
            entry["result_sha256"] = entry.get("result_sha256") or hashlib.sha256(
                str(operation.get("content", "")).encode("utf-8")
            ).hexdigest()
            backups.append(str(backup))
        entries.append(entry)

    journal = {
        "version": 1,
        "dataset": str(root),
        "state": "applying",
        "entries": entries,
    }
    _write_journal(journal_path, journal)
    applied: list[dict[str, Any]] = []

    try:
        for index, entry in enumerate(entries):
            entry["state"] = "started"
            _write_journal(journal_path, journal)
            if entry["kind"] == "rename":
                if _sha256(Path(entry["source"])) != entry.get("expected_sha256"):
                    raise BidsRepairError(
                        f"Repair plan became stale before renaming {entry['source']}."
                    )
                if entry.get("content") is not None:
                    _atomic_write(Path(entry["source"]), str(entry["content"]))
                _rename_no_replace(Path(entry["source"]), Path(entry["target"]))
            elif entry["kind"] == "write":
                if _sha256(Path(entry["source"])) != entry.get("expected_sha256"):
                    raise BidsRepairError(
                        f"Repair plan became stale before writing {entry['source']}."
                    )
                _atomic_write(Path(entry["source"]), str(entry["content"]))
            else:
                raise BidsRepairError(f"Unsupported operation kind: {entry['kind']!r}")
            entry["state"] = "done"
            _write_journal(journal_path, journal)
            diagnostics = entry.get("diagnostics") or [entry.get("diagnostic")]
            if any(isinstance(diagnostic, dict) for diagnostic in diagnostics):
                applied.extend(
                    diagnostic
                    for diagnostic in diagnostics
                    if isinstance(diagnostic, dict)
                )
            elif entry["kind"] == "write":
                applied.append(
                    {
                        "code": "REPAIRED_METADATA",
                        "severity": "info",
                        "message": f"Updated {Path(entry['source']).name}.",
                        "path": entry["source"],
                    }
                )
        journal["state"] = "complete"
        _write_journal(journal_path, journal)
    except Exception as exc:
        journal["state"] = "recovery_required"
        journal["error"] = str(exc)
        _write_journal(journal_path, journal)
        try:
            _recover_journal(journal_path)
        except Exception as recovery_exc:
            raise BidsRepairError(
                f"Repair failed ({exc}); automatic rollback also failed "
                f"({recovery_exc}). Recover with npdb repair recover {root}. "
                f"Journal: {journal_path}"
            ) from exc
        raise BidsRepairError(
            f"Repair application failed and was rolled back: {exc}. Journal: {journal_path}"
        ) from exc

    return {
        "applied": applied,
        "unresolved": unresolved,
        "journal": str(journal_path),
        "backups": backups,
    }


def recover_bids_repair(dataset: str | Path) -> dict[str, Any]:
    """Roll back the most recent incomplete repair journal for a dataset."""
    root = _validate_dataset_root(dataset)
    journal_root = _journal_root(root)
    if not journal_root.exists():
        return {"recovered": False, "journal": None, "message": "No repair journal exists."}
    if journal_root.is_symlink() or not journal_root.is_dir():
        raise BidsRepairError(f"Unsafe repair journal path: {journal_root}")
    pending = []
    for journal_path in journal_root.glob("*/journal.json"):
        if journal_path.is_symlink() or journal_path.parent.is_symlink():
            raise BidsRepairError(f"Unsafe journal symlink: {journal_path}")
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BidsRepairError(f"Cannot read repair journal {journal_path}: {exc}") from exc
        if journal.get("state") in {"applying", "recovery_required"}:
            pending.append((journal_path.stat().st_mtime_ns, journal_path))
    if not pending:
        return {"recovered": False, "journal": None, "message": "No incomplete repair journal exists."}
    journal_path = max(pending)[1]
    _recover_journal(journal_path)
    return {
        "recovered": True,
        "journal": str(journal_path),
        "message": "The most recent incomplete repair was rolled back.",
    }


def _ensure_no_incomplete_journal(journal_root: Path) -> None:
    for journal_path in journal_root.glob("*/journal.json"):
        if journal_path.is_symlink() or journal_path.parent.is_symlink():
            raise BidsRepairError(f"Unsafe journal symlink: {journal_path}")
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BidsRepairError(f"Cannot read repair journal {journal_path}: {exc}") from exc
        if journal.get("state") in {"applying", "recovery_required"}:
            raise BidsRepairError(
                f"An incomplete repair journal must be recovered before applying another plan: "
                f"{journal_path}. Run npdb repair recover {journal.get('dataset', '')}."
            )


def format_bids_repair_plan(plan: dict[str, Any]) -> str:
    """Render a compact human-readable plan."""
    lines = [f"BIDS repair plan for {plan['dataset']}"]
    if plan.get("operations"):
        lines.append(f"Planned operations: {len(plan['operations'])}")
        for operation in plan["operations"]:
            if operation["kind"] == "rename":
                lines.append(f"  Rename: {operation['source']} -> {operation['target']}")
            else:
                lines.append(f"  Update: {operation['source']}")
    else:
        lines.append("Planned operations: none")
    findings = plan.get("findings", [])
    lines.append(f"Findings: {len(findings)}")
    for finding in findings:
        path = f" [{finding['path']}]" if finding.get("path") else ""
        resolution = f" — {finding['resolution']}" if finding.get("resolution") else ""
        lines.append(
            f"  {finding['severity'].upper()} {finding['code']}{path}: "
            f"{finding['message']}{resolution}"
        )
    if plan.get("blocking"):
        lines.append("Unresolved validation errors remain.")
    return "\n".join(lines)


def _validate_dataset_root(dataset: str | Path) -> Path:
    path = Path(dataset).expanduser()
    if path.is_symlink():
        raise BidsRepairError(f"Dataset root must not be a symlink: {path}")
    try:
        root = path.resolve(strict=True)
    except OSError as exc:
        raise BidsRepairError(f"Dataset root does not exist: {path}") from exc
    if not root.is_dir():
        raise BidsRepairError(f"Dataset root is not a directory: {root}")
    return root


def _validate_aliases(aliases: dict[str, str]) -> dict[str, str]:
    if not isinstance(aliases, dict):
        raise ValueError("suffix_aliases must be a mapping of source suffix to target suffix.")
    result: dict[str, str] = {}
    for old, new in aliases.items():
        if (
            not isinstance(old, str)
            or not isinstance(new, str)
            or not old
            or not new
            or not re.fullmatch(r"[A-Za-z0-9+]+", old)
            or not re.fullmatch(r"[A-Za-z0-9+]+", new)
        ):
            raise ValueError(f"Invalid suffix alias {old!r}={new!r}.")
        if old in result and result[old] != new:
            raise ValueError(f"Conflicting suffix aliases for {old!r}.")
        result[old] = new
    chained = [
        old for old, new in result.items() if new in result and result[new] != new
    ]
    if chained:
        raise ValueError(
            "Conflicting suffix aliases form a chain or cycle: "
            + ", ".join(sorted(chained))
        )
    return result


def _normalise_finding(issue: dict[str, Any], owner: Path) -> dict[str, Any]:
    code = str(issue.get("code", "UNKNOWN_VALIDATOR_ISSUE"))
    severity = str(issue.get("severity", "info")).lower()
    if severity not in {"error", "warning", "info"}:
        severity = "info"
    finding = {
        "dataset": str(owner),
        "code": code,
        "severity": severity,
        "message": str(issue.get("message", "Validator reported an issue.")),
    }
    if issue.get("path") is not None:
        finding["path"] = str(issue["path"])
    if code not in {"INVALID_DATASET_DESCRIPTION", "INVALID_FILENAME"}:
        finding["resolution"] = "No automatic repair rule supports this validator issue."
    return finding


def _iter_issues(
    report: dict[str, Any],
) -> list[tuple[dict[str, Any], Path]]:
    records: list[tuple[dict[str, Any], Path]] = []
    for dataset_report in report.get("datasets", []):
        if not isinstance(dataset_report, dict):
            continue
        owner = Path(str(dataset_report.get("dataset", "")))
        for issue in dataset_report.get("issues", []):
            if isinstance(issue, dict):
                records.append((issue, owner))
    return records


def _issue_file_path(owner: Path, value: Any) -> Path | None:
    if not value:
        return None
    candidate = Path(str(value))
    if not candidate.is_absolute():
        candidate = owner / candidate
    candidate = Path(os.path.abspath(candidate))
    if not _is_within(owner, candidate):
        return None
    return candidate


def _normalized_filename(
    source: Path, owner: Path, aliases: dict[str, str]
) -> tuple[Path | None, str]:
    if source.is_symlink() or not source.is_file():
        return None, "The reported path is not a safe regular file."
    base, extension = _split_extension(source.name)
    if not extension:
        return None, "The filename extension is not recognized safely."
    tokens = base.split("_")
    if len(tokens) < 2:
        return None, "The filename does not contain an unambiguous entity/suffix boundary."
    suffix = tokens[-1]
    replaced_suffix = aliases.get(suffix, suffix)
    entity_tokens: list[tuple[str, str, int]] = []
    unknown_tokens: list[str] = []
    seen: dict[str, str] = {}
    order = _schema_entity_order()

    for token in tokens[:-1]:
        match = _ENTITY_TOKEN.fullmatch(token)
        if match and match.group(1) in _ENTITY_PREFIX_TO_NAME:
            prefix, value = match.groups()
            canonical = token
            entity_name = _ENTITY_PREFIX_TO_NAME[prefix]
            rank = order.get(entity_name, len(order) + _fallback_rank(prefix))
        else:
            run_match = _RUN_WITHOUT_SEPARATOR.fullmatch(token)
            if run_match:
                prefix, value, canonical = "run", run_match.group(1), f"run-{run_match.group(1)}"
                entity_name = _ENTITY_PREFIX_TO_NAME[prefix]
                rank = order.get(entity_name, len(order) + _fallback_rank(prefix))
            else:
                unknown_tokens.append(token)
                continue
        if entity_name in seen:
            if seen[entity_name] != value:
                return None, f"Conflicting filename values for entity {entity_name!r}."
            return None, f"Entity {entity_name!r} appears more than once."
        seen[entity_name] = value
        entity_tokens.append((canonical, entity_name, rank))

    inferred = _directory_entities(source, owner)
    for prefix, value in inferred.items():
        if not value:
            return None, f"Multiple enclosing directories conflict for entity {prefix!r}."
        name = _ENTITY_PREFIX_TO_NAME[prefix]
        if name in seen and seen[name] != value:
            return None, f"Filename entity conflicts with enclosing directory {prefix}-{value}."
        if name not in seen:
            entity_tokens.append(
                (f"{prefix}-{value}", name, order.get(name, len(order) + _fallback_rank(prefix)))
            )
            seen[name] = value

    if unknown_tokens:
        return None, (
            "Unrecognized filename token(s) make normalization ambiguous: "
            + ", ".join(unknown_tokens)
        )
    entity_tokens.sort(key=lambda item: (item[2], item[0]))
    normalized = "_".join([item[0] for item in entity_tokens] + [replaced_suffix])
    if normalized == base:
        return None, (
            "No supported structural correction can be inferred for this invalid filename."
        )
    return source.with_name(normalized + extension), ""


def _directory_entities(source: Path, owner: Path) -> dict[str, str]:
    inferred: dict[str, str] = {}
    try:
        parents = source.parent.relative_to(owner).parts
    except ValueError:
        return inferred
    for component in parents:
        match = _DIRECTORY_ENTITY.fullmatch(component)
        if match:
            prefix, value = match.groups()
            if prefix in inferred and inferred[prefix] != value:
                inferred[prefix] = ""
            else:
                inferred[prefix] = value
    return inferred


def _schema_entity_order() -> dict[str, int]:
    try:
        configured = list(Config.load("bids").entities)
    except Exception:
        configured = []
    order = {name: index for index, name in enumerate(configured)}
    if not order:
        order = {
            name: index
            for index, name in enumerate(
                (
                    "subject",
                    "session",
                    "sample",
                    "task",
                    "tracksys",
                    "acquisition",
                    "nucleus",
                    "volume",
                    "ceagent",
                    "staining",
                    "tracer",
                    "reconstruction",
                    "direction",
                    "run",
                    "proc",
                    "modality",
                    "echo",
                    "flip",
                    "inv",
                    "mt",
                    "part",
                    "recording",
                    "space",
                    "chunk",
                )
            )
        }
    return order


def _fallback_rank(prefix: str) -> int:
    extras = ("split", "res", "den", "hemi", "desc", "atlas", "label", "from", "to", "mode")
    try:
        return extras.index(prefix)
    except ValueError:
        return len(extras)


def _split_extension(name: str) -> tuple[str, str]:
    for extension in _EXTENSIONS:
        if name.endswith(extension):
            return name[: -len(extension)], extension
    return "", ""


def _associated_renames(source: Path, target: Path) -> tuple[dict[Path, Path], str | None]:
    old_stem, _ = _split_extension(source.name)
    new_stem, _ = _split_extension(target.name)
    if not old_stem or not new_stem:
        return {}, "The associated-file stem cannot be determined safely."
    siblings = []
    try:
        for candidate in source.parent.iterdir():
            stem, extension = _split_extension(candidate.name)
            if stem == old_stem and extension and candidate.is_file():
                if candidate.is_symlink():
                    return {}, f"Associated path is a symlink and cannot be renamed safely: {candidate}"
                siblings.append(candidate)
    except OSError as exc:
        return {}, f"Cannot inspect associated files: {exc}"
    images = [item for item in siblings if _split_extension(item.name)[1] in _IMAGE_EXTENSIONS]
    if len(images) > 1:
        return {}, "Multiple image files share this stem; associated files cannot be linked unambiguously."
    if source not in siblings:
        siblings.append(source)
    renames: dict[Path, Path] = {}
    for item in siblings:
        _, extension = _split_extension(item.name)
        replacement = item.with_name(new_stem + extension)
        if replacement != item:
            renames[item] = replacement
    return renames, None


def _intendedfor_updates(
    dataset_root: Path,
    rename_map: dict[str, str],
    owner_roots: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    operations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    pairs: dict[str, dict[str, str]] = {}
    for old, new in rename_map.items():
        owner = Path(owner_roots.get(old, str(dataset_root)))
        old_path = Path(old)
        new_path = Path(new)
        try:
            old_rel = old_path.relative_to(owner).as_posix()
            new_rel = new_path.relative_to(owner).as_posix()
        except ValueError:
            continue
        pairs[str(owner)] = pairs.get(str(owner), {})
        pairs[str(owner)][old_rel] = new_rel
    for owner_string, renames in sorted(pairs.items()):
        owner = Path(owner_string)
        try:
            json_files = sorted(owner.rglob("*.json"))
        except OSError as exc:
            findings.append(
                _finding(
                    "INTENDEDFOR_SCAN_FAILED",
                    "warning",
                    f"Could not scan JSON references: {exc}",
                    path=str(owner),
                    resolution="IntendedFor references were not updated.",
                )
            )
            continue
        for path in json_files:
            if path.is_symlink() or not path.is_file():
                continue
            try:
                original = path.read_bytes()
                data = json.loads(original)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            updated, changed = _replace_intendedfor(data, renames)
            if not changed:
                continue
            serialized = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"
            source_hash = hashlib.sha256(original).hexdigest()
            operations.append(
                _operation(
                    "write",
                    path,
                    None,
                    content=serialized,
                    expected_sha256=source_hash,
                    affected_paths=[str(path)],
                    evidence="Updated exact IntendedFor paths corresponding to planned file renames.",
                    diagnostic={
                        "code": "UPDATED_INTENDEDFOR",
                        "severity": "info",
                        "message": f"Updated IntendedFor reference(s) in {path.name}.",
                        "path": str(path),
                    },
                )
            )
    return operations, findings


def _replace_intendedfor(value: Any, renames: dict[str, str]) -> tuple[Any, bool]:
    changed = False
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key == "IntendedFor":
                replaced, did_change = _replace_reference_value(child, renames)
                result[key] = replaced
                changed |= did_change
            else:
                result[key] = child
        return result, changed
    if isinstance(value, list):
        result = []
        for child in value:
            replaced, did_change = _replace_intendedfor(child, renames)
            result.append(replaced)
            changed |= did_change
        return result, changed
    return value, False


def _replace_reference_value(value: Any, renames: dict[str, str]) -> tuple[Any, bool]:
    if isinstance(value, str):
        prefix = "bids::" if value.startswith("bids::") else ""
        path_value = value[len(prefix) :]
        if path_value in renames:
            return prefix + renames[path_value], True
        return value, False
    if isinstance(value, list):
        result = []
        changed = False
        for item in value:
            replaced, did_change = _replace_reference_value(item, renames)
            result.append(replaced)
            changed |= did_change
        return result, changed
    return value, False


def _description_repair(
    owner: Path, bids_version: str | None
) -> tuple[dict[str, Any] | None, str]:
    path = owner / "dataset_description.json"
    if path.is_symlink() or not path.is_file():
        return None, "dataset_description.json is missing or unsafe; no repair was proposed."
    try:
        original = path.read_bytes()
        document = json.loads(original)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, (
            f"dataset_description.json contains invalid JSON or cannot be read: {exc}. "
            "It cannot be repaired speculatively."
        )
    if not isinstance(document, dict):
        return None, "dataset_description.json must contain a JSON object; no safe repair was inferred."

    changes: list[str] = []
    schema_problems = inspect_dataset_description(owner)["problems"]
    unsupported_authors = None
    if isinstance(document.get("Authors"), str):
        document["Authors"] = [document["Authors"]]
        changes.append("wrapped the Authors string as one array entry")
    elif "Authors" in document and not (
        isinstance(document["Authors"], list)
        and all(isinstance(author, str) for author in document["Authors"])
    ):
        unsupported_authors = "Authors has an unsupported type; no value was discarded or guessed."

    unresolved_version = None
    if "BIDSVersion" not in document:
        if bids_version:
            document["BIDSVersion"] = bids_version
            changes.append(f"added explicitly supplied BIDSVersion {bids_version}")
        else:
            unresolved_version = (
                "BIDSVersion is missing; supply --bids-version explicitly. "
                "The repair engine does not guess a version."
            )
    elif not isinstance(document["BIDSVersion"], str) or not document["BIDSVersion"].strip():
        if bids_version:
            document["BIDSVersion"] = bids_version
            changes.append(f"replaced invalid BIDSVersion with explicitly supplied {bids_version}")
        else:
            unresolved_version = "BIDSVersion has an invalid type; supply --bids-version explicitly."

    if not changes:
        explanation = "; ".join(
            item
            for item in (
                unsupported_authors,
                unresolved_version,
                "JSON syntax is valid; " + (
                    "; ".join(schema_problems)
                    if schema_problems
                    else "basic field checks passed, but the structural validator still "
                    "rejects the description. No supported, lossless "
                    "dataset-description normalization was identified."
                )
                if not unsupported_authors and not unresolved_version
                else None,
            )
            if item
        )
        return None, explanation
    content = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    source_hash = hashlib.sha256(original).hexdigest()
    operation = _operation(
        "write",
        path,
        None,
        content=content,
        expected_sha256=source_hash,
        affected_paths=[str(path)],
        evidence="Parsed dataset_description.json; retained all existing fields and values except the documented safe type normalization.",
        diagnostic={
            "code": "REPAIRED_DATASET_DESCRIPTION",
            "severity": "info",
            "message": f"Updated dataset_description.json ({'; '.join(changes)}).",
            "path": str(path),
            "affected_paths": [str(path)],
        },
    )
    explanation = "; ".join(changes)
    if unsupported_authors:
        explanation += f"; {unsupported_authors}"
    if unresolved_version:
        explanation += f"; {unresolved_version}"
    return operation, explanation


def inspect_dataset_description(owner: Path) -> dict[str, Any]:
    """Explain JSON syntax and common schema failures without inventing metadata."""
    path = owner / "dataset_description.json"
    diagnostics: dict[str, Any] = {"path": str(path), "json_valid": False, "problems": []}
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
        "raw", "derivative", "study"
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
                elif not isinstance(generator.get("Name"), str) or not generator["Name"].strip():
                    problems.append(f"GeneratedBy[{index}].Name must be a non-empty string.")
    diagnostics["scope"] = "JSON syntax and common field checks; not full BIDS compliance."
    return diagnostics


def _operation(
    kind: str,
    source: Path,
    target: Path | None,
    *,
    expected_sha256: str,
    affected_paths: list[str],
    evidence: str,
    content: str | None = None,
    diagnostic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    operation = {
        "kind": kind,
        "source": str(source),
        "target": str(target) if target else None,
        "affected_paths": sorted(set(affected_paths)),
        "evidence": evidence,
        "expected_sha256": expected_sha256,
    }
    if content is not None:
        operation["content"] = content
    if diagnostic is not None:
        operation["diagnostic"] = diagnostic
    identity = json.dumps(
        [kind, str(source), str(target), expected_sha256], separators=(",", ":")
    )
    operation["id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return operation


def _preflight_operations(root: Path, operations: list[dict[str, Any]]) -> None:
    targets: set[str] = set()
    sources: set[str] = set()
    rename_sources = {
        str(Path(item.get("source", ""))) for item in operations if item.get("kind") == "rename"
    }
    for operation in operations:
        if not isinstance(operation, dict):
            raise BidsRepairError("Repair plan contains a malformed operation.")
        kind = operation.get("kind")
        source = Path(str(operation.get("source", "")))
        _assert_safe_path(root, source, must_exist=True)
        if not source.is_file():
            raise BidsRepairError(f"Repair source is not a regular file: {source}")
        source_key = str(source)
        if source_key in sources:
            raise BidsRepairError(f"Repair plan modifies a source more than once: {source}")
        sources.add(source_key)
        try:
            actual_hash = _sha256(source)
        except OSError as exc:
            raise BidsRepairError(f"Cannot hash repair source {source}: {exc}") from exc
        if actual_hash != operation.get("expected_sha256"):
            raise BidsRepairError(
                f"Repair plan is stale: {source} changed since planning."
            )
        if kind == "rename":
            target = Path(str(operation.get("target", "")))
            _assert_safe_path(root, target, must_exist=False)
            key = str(target)
            if key in targets:
                raise BidsRepairError(f"Multiple operations target the same path: {target}")
            targets.add(key)
            if target.exists() and key not in rename_sources:
                raise BidsRepairError(f"Repair would overwrite an existing file: {target}")
            if target.is_symlink():
                raise BidsRepairError(f"Repair target is a symlink: {target}")
            if operation.get("content") is not None and not isinstance(
                operation.get("content"), str
            ):
                raise BidsRepairError("Combined rename/write operation has invalid content.")
        elif kind == "write":
            if operation.get("target") not in (None, ""):
                raise BidsRepairError("Metadata write operation must not specify a target.")
            if not isinstance(operation.get("content"), str):
                raise BidsRepairError("Metadata write operation has no text content.")
        else:
            raise BidsRepairError(f"Unsupported repair operation: {kind!r}")
    if targets.intersection(sources):
        raise BidsRepairError(
            "Repair plan contains overlapping rename sources and targets; refusing an ambiguous rename."
        )


def _assert_safe_path(root: Path, path: Path, *, must_exist: bool) -> None:
    absolute = Path(os.path.abspath(path))
    if not _is_within(root, absolute):
        raise BidsRepairError(f"Repair path escapes the dataset root: {path}")
    relative = absolute.relative_to(root)
    current = root
    if root.is_symlink():
        raise BidsRepairError(f"Dataset root is a symlink: {root}")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise BidsRepairError(f"Repair path contains a symlink: {current}")
    if must_exist and not absolute.exists():
        raise BidsRepairError(f"Repair source no longer exists: {absolute}")


def _contains_symlink(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    if current.is_symlink():
        return True
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _recover_journal(journal_path: Path) -> None:
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BidsRepairError(f"Cannot read repair journal {journal_path}: {exc}") from exc
    if journal.get("version") != 1 or not isinstance(journal.get("entries"), list):
        raise BidsRepairError(f"Unsupported or malformed repair journal: {journal_path}")
    root = _validate_dataset_root(journal.get("dataset", ""))
    for entry in reversed(journal["entries"]):
        if not isinstance(entry, dict) or entry.get("state") == "pending":
            continue
        kind = entry.get("kind")
        source = Path(str(entry.get("source", "")))
        if kind == "rename":
            target = Path(str(entry.get("target", "")))
            _assert_safe_path(root, source, must_exist=False)
            _assert_safe_path(root, target, must_exist=False)
            source_exists, target_exists = source.exists(), target.exists()
            changed_hash = entry.get("result_sha256") or entry.get("expected_sha256")
            if source_exists and target_exists:
                if (
                    os.path.samefile(source, target)
                    and _sha256(source) == changed_hash
                    and _sha256(target) == changed_hash
                ):
                    target.unlink()
                    if entry.get("backup"):
                        _atomic_write_bytes(source, Path(entry["backup"]).read_bytes())
                else:
                    raise BidsRepairError(
                        f"Cannot safely recover rename; both paths exist with changed content: {source}, {target}"
                    )
            elif target_exists:
                if _sha256(target) != changed_hash:
                    raise BidsRepairError(
                        f"Cannot safely recover rename; target changed: {target}"
                    )
                _rename_no_replace(target, source)
                if entry.get("backup"):
                    _atomic_write_bytes(source, Path(entry["backup"]).read_bytes())
            elif not source_exists:
                raise BidsRepairError(
                    f"Cannot recover rename; neither source nor target exists: {source}, {target}"
                )
            elif entry.get("backup"):
                current_hash = _sha256(source)
                if current_hash == changed_hash:
                    _atomic_write_bytes(source, Path(entry["backup"]).read_bytes())
                elif current_hash != entry.get("expected_sha256"):
                    raise BidsRepairError(
                        f"Cannot restore renamed metadata changed during recovery: {source}"
                    )
        elif kind == "write":
            _assert_safe_path(root, source, must_exist=False)
            backup = Path(str(entry.get("backup", "")))
            expected_backup = journal_path.parent / "backups"
            if (
                backup.is_symlink()
                or not backup.is_file()
                or not _is_within(expected_backup, backup)
                or _contains_symlink(journal_path.parent, backup)
                or _sha256(backup) != entry.get("expected_sha256")
            ):
                raise BidsRepairError(f"Metadata backup is missing or unsafe: {backup}")
            if source.exists():
                current_hash = _sha256(source)
                original_hash = entry.get("expected_sha256")
                result_hash = entry.get("result_sha256")
                if current_hash == original_hash:
                    pass
                elif current_hash == result_hash:
                    _atomic_write_bytes(source, backup.read_bytes())
                else:
                    raise BidsRepairError(
                        f"Cannot safely restore metadata changed after repair: {source}"
                    )
            else:
                raise BidsRepairError(f"Metadata file is missing during recovery: {source}")
        else:
            raise BidsRepairError(f"Unknown journal operation: {kind!r}")
        entry["state"] = "rolled_back"
        _write_journal(journal_path, journal)
    journal["state"] = "recovered"
    _write_journal(journal_path, journal)


def _rename_no_replace(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise BidsRepairError(f"Refusing to overwrite existing repair target: {target}")
    try:
        os.link(source, target, follow_symlinks=False)
        source.unlink()
    except FileExistsError as exc:
        raise BidsRepairError(f"Refusing to overwrite existing repair target: {target}") from exc
    except OSError as exc:
        raise BidsRepairError(f"Cannot safely rename {source} to {target}: {exc}") from exc


def _atomic_write(path: Path, content: str) -> None:
    _atomic_write_bytes(path, content.encode("utf-8"))


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.npdb-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            if path.exists():
                os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode))
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_journal(path: Path, journal: dict[str, Any]) -> None:
    content = json.dumps(journal, indent=2, sort_keys=True) + "\n"
    _atomic_write(path, content)


def _journal_root(root: Path) -> Path:
    return root.parent / f".{root.name}.npdb-bids-repair"


def save_bids_repair_manifest(
    dataset: str | Path, report: dict[str, Any]
) -> Path:
    """Persist validation diagnostics beside the latest journal, even without edits."""
    root = _validate_dataset_root(dataset)
    journal_root = _journal_root(root)
    if any(path.is_symlink() for path in (journal_root, *journal_root.parents)):
        raise BidsRepairError(f"Refusing symlink manifest directory: {journal_root}")
    journal_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    journal = report["repair"].get("journal")
    if journal:
        directory = Path(journal).parent
        _assert_safe_path(journal_root, directory, must_exist=True)
    else:
        directory = journal_root / uuid.uuid4().hex
        directory.mkdir(mode=0o700)
    manifest = directory / "validation_manifest.json"
    if manifest.exists() or manifest.is_symlink():
        raise BidsRepairError(f"Refusing to overwrite validation manifest: {manifest}")
    report["repair"]["manifest"] = str(manifest)
    try:
        _write_journal(manifest, report)
    except OSError as exc:
        raise BidsRepairError(f"Cannot save BIDS validation manifest at {manifest}: {exc}") from exc
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _finding(
    code: str, severity: str, message: str, **details: Any
) -> dict[str, Any]:
    finding = {"code": code, "severity": severity, "message": message}
    finding.update({key: value for key, value in details.items() if value is not None})
    return finding

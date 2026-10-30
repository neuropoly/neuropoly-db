import json
import os
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import Any

from bids_repair.bids_version import resolve_bids_version
from bids_repair.native import (
    COVERAGE,
    RUST_VERSION,
    schema_document,
    validator_executable,
)


class BidsSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class BidsValidationError(RuntimeError):
    """Final validation exceeded the configured threshold; retains the audit."""

    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = report


def _is_derivative_finding(
    finding: dict[str, Any], *, dataset_root: Path | None
) -> bool:
    if dataset_root is None:
        return False

    derivatives_root = dataset_root / "derivatives"
    raw_dataset = finding.get("dataset")
    if raw_dataset:
        owner = Path(str(raw_dataset))
        if not owner.is_absolute():
            owner = dataset_root / owner
        if owner.is_relative_to(derivatives_root):
            return True
        if owner == dataset_root and dataset_root.parent.name == "derivatives":
            return True

    raw_path = finding.get("path")
    if raw_path in (None, ""):
        return False

    path = Path(str(raw_path))
    if not path.is_absolute():
        path = dataset_root / path
    return path.is_relative_to(derivatives_root)


def _effective_severity(
    finding: dict[str, Any], *, dataset_root: Path | None
) -> BidsSeverity:
    try:
        severity = BidsSeverity(str(finding.get("severity", "")).lower())
    except ValueError as exc:
        raise RuntimeError(
            f"Unknown BIDS finding severity: {finding.get('severity')!r}"
        ) from exc

    if severity == BidsSeverity.ERROR and _is_derivative_finding(
        finding, dataset_root=dataset_root
    ):
        return BidsSeverity.WARNING
    return severity


def _effective_finding(
    finding: dict[str, Any], *, dataset_root: Path | None
) -> dict[str, Any]:
    adjusted = dict(finding)
    severity = _effective_severity(finding, dataset_root=dataset_root)
    if severity.value != str(finding.get("severity", "")).lower():
        adjusted["severity"] = severity.value
        adjusted["original_severity"] = finding["severity"]
    return adjusted


def findings_above_threshold(
    report: dict[str, Any],
    severity_threshold: BidsSeverity | str = BidsSeverity.WARNING,
    *,
    dataset_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return final findings strictly more severe than the allowed threshold."""
    threshold = BidsSeverity(severity_threshold)
    ranks = {severity: rank for rank, severity in enumerate(BidsSeverity)}
    findings = _normalised_report_issues(report, dataset_root=dataset_root)
    for dataset_report in report["datasets"]:
        issues = dataset_report.get("issues", [])
        for severity, count_key in (
            (BidsSeverity.ERROR, "errors"),
            (BidsSeverity.WARNING, "warnings"),
        ):
            count = dataset_report.get(count_key, 0)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise RuntimeError(
                    f"Invalid BIDS validator {count_key} count: {count!r}"
                )
            listed = sum(
                str(issue.get("severity", "")).lower() == severity.value
                for issue in issues
                if isinstance(issue, dict)
            )
            if count > listed:
                findings.append(
                    {
                        "code": "UNLISTED_VALIDATOR_FINDINGS",
                        "severity": severity.value,
                        "dataset": str(dataset_report.get("dataset", "")),
                        "path": str(dataset_report.get("dataset", "")),
                        "message": f"Validator reported {count - listed} additional {severity.value} finding(s).",
                    }
                )

    findings.extend(
        _effective_finding(item, dataset_root=dataset_root)
        for item in report.get("repair", {}).get("unresolved", [])
    )
    exceeding = {}
    for finding in findings:
        finding = _effective_finding(finding, dataset_root=dataset_root)
        severity = BidsSeverity(finding["severity"])
        if ranks[severity] > ranks[threshold]:
            exceeding[_issue_key(finding)] = finding

    return list(exceeding.values())


def get_bids_validation_report(
    dataset: str | Path,
    *,
    target_version: str | None = None,
    schema_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return the validator's structured report, including reports with findings."""
    if schema_path is not None and target_version is None:
        raise ValueError("schema_path requires target_version")

    from bids_repair.paths import assert_safe_path
    from bids_repair.target import acquire_target_schema

    if target_version is not None:
        acquire_target_schema(
            target_version, Path(schema_path) if schema_path is not None else None
        )
    root = Path(dataset).resolve()
    if not root.is_dir():
        raise ValueError(f"Dataset is not a directory: {root}")
    executable = _validator_executable()
    roots = [root]
    derivatives = root / "derivatives"
    if derivatives.is_dir():
        assert_safe_path(root, derivatives, must_exist=True)
        for child in sorted(derivatives.iterdir()):
            if child.is_dir():
                assert_safe_path(root, child, must_exist=True)
                roots.append(child)
    report: dict[str, Any] = {
        "validator": f"rordenlab/bids-validator-rs {RUST_VERSION}",
        "coverage": COVERAGE,
        "schema_bids_version": schema_document()["bids_version"],
        "datasets": [_native_dataset_report(executable, owner) for owner in roots],
    }
    for derivative in report["datasets"][1:]:
        _check_derivative_provenance(derivative)
    _explain_description_findings(root, report)
    if target_version is not None:
        report["target_bids_version"] = target_version
    return report


def _check_derivative_provenance(report: dict[str, Any]) -> None:
    from bids_repair.description import read_dataset_description

    owner = Path(report["dataset"])
    try:
        description = (
            read_dataset_description(owner)
            if (owner / "dataset_description.json").exists()
            else {}
        )
    except (OSError, ValueError) as exc:
        detail = f"Cannot read derivative provenance: {exc}"
    else:
        generators = description.get("GeneratedBy")
        if (
            isinstance(generators, list)
            and generators
            and isinstance(generators[0], dict)
            and isinstance(generators[0].get("Name"), str)
            and generators[0]["Name"].strip()
        ):
            return
        detail = "Every derivative dataset must declare GeneratedBy[0].Name."
    report["issues"].append(
        {
            "code": "DERIVATIVE_PROVENANCE",
            "severity": "error",
            "path": str(owner / "dataset_description.json"),
            "message": detail,
        }
    )
    report["errors"] += 1


def _native_dataset_report(executable: str, owner: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            executable,
            str(owner),
            "--format",
            "json",
            "--content-mode",
            "parity",
            "--link-mode",
            "parity",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        detail = result.stdout or result.stderr or "no output"
        raise RuntimeError(
            f"Rust BIDS validator did not return a structured report: {detail}"
        ) from exc
    if not isinstance(document, dict) or not isinstance(document.get("issues"), dict):
        raise RuntimeError(
            f"Rust BIDS validator returned an invalid report: {result.stdout}"
        )
    envelope = document["issues"]
    issues = envelope.get("issues")
    messages = envelope.get("codeMessages")
    if (
        not isinstance(issues, list)
        or not isinstance(messages, dict)
        or any(not isinstance(value, str) for value in messages.values())
    ):
        raise RuntimeError("Rust BIDS validator returned invalid issues/codeMessages.")

    normalized = []
    for issue in issues:
        if (
            not isinstance(issue, dict)
            or not isinstance(issue.get("code"), str)
            or not issue["code"]
            or issue.get("severity") not in ("error", "warning")
            or not isinstance(issue.get("location", ""), str)
            or (
                issue.get("issueMessage") is not None
                and not isinstance(issue["issueMessage"], str)
            )
        ):
            raise RuntimeError(
                f"Rust BIDS validator returned an invalid issue: {issue!r}"
            )
        location = issue.get("location", "").lstrip("/")
        relative = Path(location)
        if relative.is_absolute() or ".." in relative.parts or "\\" in location:
            raise RuntimeError(f"Unsafe Rust validator issue location: {location!r}")
        message = messages.get(issue["code"])
        detail = issue.get("issueMessage")
        if detail:
            message = f"{message}: {detail}" if message else detail
        if not message:
            raise RuntimeError(
                f"Rust validator supplied no message for {issue['code']}."
            )
        normalized.append(
            {
                **issue,
                "path": str(owner / relative),
                "message": message,
                "code_message": messages.get(issue["code"]),
            }
        )
    errors = sum(issue["severity"] == "error" for issue in normalized)
    if result.returncode not in (0, 1) or bool(result.returncode) != bool(errors):
        raise RuntimeError(
            "Rust BIDS validator exit status disagrees with its error findings: "
            f"{result.returncode}: {result.stdout or result.stderr}"
        )
    return {
        "dataset": str(owner),
        "issues": normalized,
        "errors": errors,
        "warnings": len(normalized) - errors,
    }


def _explain_description_findings(root: Path, report: dict[str, Any]) -> None:
    from bids_repair.description import inspect_dataset_description
    from bids_repair.paths import assert_safe_path
    from bids_repair.repair import issue_repair_kind

    root = root.resolve()
    for dataset_report in report["datasets"]:
        owner = Path(dataset_report.get("dataset", root))
        if not owner.is_absolute():
            owner = root / owner
        assert_safe_path(root, owner, must_exist=True)
        for issue in dataset_report.get("issues", []):
            if issue_repair_kind(issue, owner) != "description":
                continue

            diagnostics = inspect_dataset_description(owner)
            issue["diagnostics"] = diagnostics
            detail = "; ".join(diagnostics["problems"]) or (
                "JSON syntax and basic field checks passed; the native "
                "validator still reports a description issue."
            )
            issue["message"] = f"{issue['message']}: {detail}"


def validate_bids_dataset(
    dataset: str | Path,
    *,
    target_version: str | None = None,
    schema_path: str | Path | None = None,
) -> dict[str, Any]:
    if schema_path is not None and target_version is None:
        raise ValueError("schema_path requires target_version")

    report = (
        get_bids_validation_report(
            dataset, target_version=target_version, schema_path=schema_path
        )
        if target_version is not None
        else get_bids_validation_report(dataset)
    )
    if findings_above_threshold(report, dataset_root=Path(dataset).resolve()):
        raise RuntimeError(f"Rust BIDS validation failed: {json.dumps(report)}")
    return report


def validate_and_repair_bids_dataset(
    dataset: str | Path,
    *,
    bids_version: str | None = None,
    suffix_aliases: dict[str, str] | None = None,
    severity_threshold: BidsSeverity | str = BidsSeverity.WARNING,
    target_version: str | None = None,
    schema_path: str | Path | None = None,
) -> dict[str, Any]:
    """Apply at most two safe repair passes, revalidate, and retain diagnostics."""
    from bids_repair.repair import (
        _plan_from_report,
        _validate_aliases,
        _validate_dataset_root,
        apply_bids_repair,
        save_bids_repair_manifest,
    )

    threshold = BidsSeverity(severity_threshold)
    root = _validate_dataset_root(dataset)
    requested_bids_version = bids_version
    bids_version = resolve_bids_version(bids_version)
    aliases = _validate_aliases(suffix_aliases if suffix_aliases is not None else {})
    if schema_path is not None and target_version is None:
        raise ValueError("schema_path requires target_version")

    def validation_report() -> dict[str, Any]:
        if target_version is not None:
            return get_bids_validation_report(
                root, target_version=target_version, schema_path=schema_path
            )
        return get_bids_validation_report(root)

    try:
        original_report = validation_report()
    except RuntimeError as exc:
        raise RuntimeError(f"BIDS validation could not be completed: {exc}") from exc

    current_report = original_report
    applied: list[dict[str, Any]] = []
    applied_operations: list[dict[str, Any]] = []
    journals: list[str] = []
    backups: list[str] = []
    seen_plans: set[tuple[str, ...]] = set()

    for _ in range(2):
        plan = _plan_from_report(
            root,
            current_report,
            bids_version=bids_version,
            suffix_aliases=aliases,
        )
        operations = plan["operations"]
        if not operations:
            break
        signature = tuple(item["id"] for item in operations)
        if signature in seen_plans:
            break
        seen_plans.add(signature)
        try:
            result = apply_bids_repair(plan)
        except Exception as exc:
            raise RuntimeError(f"BIDS repair application failed: {exc}") from exc
        applied.extend(result["applied"])
        applied_operations.extend(operations)
        if result.get("journal"):
            journals.append(result["journal"])
        backups.extend(result.get("backups", []))
        try:
            current_report = validation_report()
        except RuntimeError as exc:
            raise RuntimeError(
                f"BIDS repair was applied but revalidation failed; recovery information "
                f"is available at {journals[-1] if journals else 'the repair journal'}: {exc}"
            ) from exc

    original_keys = _report_issue_keys(original_report, dataset_root=root)
    final_keys = _report_issue_keys(current_report, dataset_root=root)
    final_plan = _plan_from_report(
        root,
        current_report,
        bids_version=bids_version,
        suffix_aliases=aliases,
    )
    unresolved_candidates = [
        item for item in final_plan["findings"] if item.get("resolution") != "planned"
    ]
    unresolved = _remaining_findings(
        current_report, unresolved_candidates, original_keys, dataset_root=root
    )
    resolved = [
        finding
        for finding in _normalised_report_issues(original_report, dataset_root=root)
        if _issue_key(finding) not in final_keys
    ]
    newly_introduced = [
        finding for finding in unresolved if _issue_key(finding) not in original_keys
    ]

    repair_summary = {
        "requested_bids_version": requested_bids_version,
        "resolved_bids_version": bids_version,
        "applied": applied,
        "operations": applied_operations,
        "unresolved": unresolved,
        "resolved": resolved,
        "newly_introduced": newly_introduced,
        "original_report": original_report,
        "final_report": current_report,
        "journal": journals[-1] if journals else None,
        "journals": journals,
        "backups": backups,
    }
    final_report = dict(current_report)
    final_report["repair"] = repair_summary
    exceeding = findings_above_threshold(final_report, threshold, dataset_root=root)
    repair_summary["severity_gate"] = {
        "threshold": threshold.value,
        "passed": not exceeding,
        "exceeding": exceeding,
    }

    counts = _final_finding_counts(final_report, dataset_root=root)
    repair_summary["counts"] = counts
    manifest = save_bids_repair_manifest(root, final_report)
    print(
        f"BIDS repair: {len(applied_operations)} operation(s) applied; final findings: "
        f"info={counts['info']}, warning={counts['warning']}, error={counts['error']}.",
        file=sys.stderr,
    )
    print(f"BIDS validation manifest: {manifest}", file=sys.stderr)

    if exceeding:
        raise BidsValidationError(
            f"BIDS validation still has {len(exceeding)} finding(s) above "
            f"'{threshold.value}' after safe repair. See {manifest}",
            final_report,
        )
    return final_report


def _final_finding_counts(
    report: dict[str, Any], *, dataset_root: Path | None = None
) -> dict[str, int]:
    counts = {severity.value: 0 for severity in BidsSeverity}
    findings = {
        _issue_key(finding): finding
        for finding in [
            *_normalised_report_issues(report, dataset_root=dataset_root),
            *(
                _effective_finding(item, dataset_root=dataset_root)
                for item in report["repair"]["unresolved"]
            ),
        ]
    }
    for finding in findings.values():
        severity = BidsSeverity(str(finding["severity"]).lower())
        counts[severity.value] += 1

    for dataset_report in report["datasets"]:
        for severity, field in (("warning", "warnings"), ("error", "errors")):
            listed = sum(
                str(issue.get("severity", "")).lower() == severity
                for issue in dataset_report.get("issues", [])
                if isinstance(issue, dict)
            )
            remaining = max(0, dataset_report.get(field, 0) - listed)
            if remaining:
                effective = _effective_severity(
                    {
                        "severity": severity,
                        "dataset": str(dataset_report.get("dataset", "")),
                    },
                    dataset_root=dataset_root,
                )
                counts[effective.value] += remaining

    return counts


def _remaining_findings(
    report: dict[str, Any],
    candidates: list[dict[str, Any]],
    original_keys: set[tuple[str, str, str, str]],
    *,
    dataset_root: Path | None = None,
) -> list[dict[str, Any]]:
    candidate_index: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    candidate_by_location: dict[tuple[str, str, str], dict[str, Any]] = {}
    for finding in candidates:
        key = _issue_key(finding)
        candidate_index[key] = finding
        candidate_by_location[key[:3]] = finding

    remaining: list[dict[str, Any]] = []
    for dataset_report in report.get("datasets", []):
        dataset = str(dataset_report.get("dataset", ""))
        for issue in dataset_report.get("issues", []):
            if not isinstance(issue, dict):
                continue
            key = (
                dataset,
                str(issue.get("code", "")),
                str(issue.get("path") or ""),
                str(issue.get("message", "")),
            )
            previous = candidate_index.get(key) or candidate_by_location.get(key[:3])
            finding = {
                "dataset": dataset,
                "code": key[1] or "UNKNOWN_VALIDATOR_ISSUE",
                "severity": str(issue.get("severity", "info")).lower(),
                "message": key[3] or "Validator reported an issue.",
            }
            if key[2]:
                finding["path"] = key[2]
            if issue.get("diagnostics"):
                finding["diagnostics"] = issue["diagnostics"]
            elif previous and previous.get("diagnostics"):
                finding["diagnostics"] = previous["diagnostics"]
            if previous and previous.get("resolution") not in (None, "planned"):
                finding["resolution"] = previous["resolution"]
            elif _issue_key(finding) not in original_keys:
                finding["resolution"] = (
                    "New finding appeared during revalidation; no safe automatic repair is available."
                )
            else:
                finding["resolution"] = (
                    "The finding remains after the bounded automatic repair pass."
                )
            remaining.append(_effective_finding(finding, dataset_root=dataset_root))

    from bids_repair.repair import issue_repair_kind

    for finding in candidates:
        if issue_repair_kind(finding) is None:
            remaining.append(_effective_finding(finding, dataset_root=dataset_root))

    deduplicated = {_issue_key(item): item for item in remaining}
    return sorted(
        deduplicated.values(),
        key=lambda item: (
            str(item.get("path", "")),
            str(item.get("code", "")),
            str(item.get("message", "")),
        ),
    )


def _normalised_report_issues(
    report: dict[str, Any], *, dataset_root: Path | None = None
) -> list[dict[str, Any]]:
    findings = []
    for dataset_report in report.get("datasets", []):
        for issue in dataset_report.get("issues", []):
            if not isinstance(issue, dict):
                continue
            finding = {
                "dataset": str(dataset_report.get("dataset", "")),
                "code": str(issue.get("code", "UNKNOWN_VALIDATOR_ISSUE")),
                "severity": str(issue.get("severity", "info")).lower(),
                "message": str(issue.get("message", "Validator reported an issue.")),
            }
            if issue.get("path") is not None:
                finding["path"] = str(issue["path"])
            findings.append(_effective_finding(finding, dataset_root=dataset_root))
    return findings


def _report_issue_keys(
    report: dict[str, Any], *, dataset_root: Path | None = None
) -> set[tuple[str, str, str, str]]:
    return {
        _issue_key(item)
        for item in _normalised_report_issues(report, dataset_root=dataset_root)
    }


def _issue_key(finding: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(finding.get("dataset", "")),
        str(finding.get("code", "")),
        str(finding.get("path") or ""),
        str(finding.get("message", "")),
    )


def _validator_executable() -> str:
    return validator_executable()

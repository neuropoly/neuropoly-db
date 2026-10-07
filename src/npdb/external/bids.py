import json
import os
import shutil
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import Any

from npdb.external.bids_version import resolve_bids_version


class BidsSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class BidsValidationError(RuntimeError):
    """Final validation exceeded the configured threshold; retains the audit."""

    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = report


def findings_above_threshold(
    report: dict[str, Any],
    severity_threshold: BidsSeverity | str = BidsSeverity.WARNING,
) -> list[dict[str, Any]]:
    """Return final findings strictly more severe than the allowed threshold."""
    threshold = BidsSeverity(severity_threshold)
    ranks = {severity: rank for rank, severity in enumerate(BidsSeverity)}
    findings = _normalised_report_issues(report)
    for dataset_report in report["datasets"]:
        issues = dataset_report.get("issues", [])
        for severity, count_key in (
            (BidsSeverity.ERROR, "errors"),
            (BidsSeverity.WARNING, "warnings"),
        ):
            count = dataset_report.get(count_key, 0)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise RuntimeError(f"Invalid BIDS validator {count_key} count: {count!r}")
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
                        "path": str(dataset_report.get("dataset", "")),
                        "message": f"Validator reported {count - listed} additional {severity.value} finding(s).",
                    }
                )
    findings.extend(report.get("repair", {}).get("unresolved", []))
    exceeding = {}
    for finding in findings:
        try:
            severity = BidsSeverity(str(finding.get("severity", "")).lower())
        except ValueError as exc:
            raise RuntimeError(
                f"Unknown BIDS finding severity: {finding.get('severity')!r}"
            ) from exc
        if ranks[severity] > ranks[threshold]:
            exceeding[_issue_key(finding)] = finding
    return list(exceeding.values())


def get_bids_validation_report(dataset: str | Path) -> dict[str, Any]:
    """Return the validator's structured report, including reports with findings."""
    executable = _validator_executable()
    result = subprocess.run(
        [executable, str(Path(dataset).resolve())],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        detail = result.stdout or result.stderr or "no output"
        raise RuntimeError(
            f"Rust BIDS validator did not return a structured report: {detail}"
        ) from exc
    if (
        not isinstance(report, dict)
        or not isinstance(report.get("datasets"), list)
        or not report["datasets"]
        or any(not isinstance(item, dict) for item in report["datasets"])
    ):
        raise RuntimeError(f"Rust BIDS validator returned an invalid report: {result.stdout}")
    errors = sum(
        int(item.get("errors", 0))
        for item in report["datasets"]
        if isinstance(item.get("errors", 0), (int, float))
    )
    if result.returncode and errors == 0:
        raise RuntimeError(
            "Rust BIDS validator exited unsuccessfully without reporting validation "
            f"errors: {result.stdout or result.stderr}"
        )
    _explain_description_findings(Path(dataset), report)
    return report


def _explain_description_findings(root: Path, report: dict[str, Any]) -> None:
    from npdb.bids_repair import _assert_safe_path, inspect_dataset_description

    root = root.resolve()
    for dataset_report in report["datasets"]:
        owner = Path(dataset_report.get("dataset", root))
        if not owner.is_absolute():
            owner = root / owner
        _assert_safe_path(root, owner, must_exist=True)
        for issue in dataset_report.get("issues", []):
            if issue.get("code") != "INVALID_DATASET_DESCRIPTION":
                continue
            diagnostics = inspect_dataset_description(owner)
            issue["diagnostics"] = diagnostics
            detail = "; ".join(diagnostics["problems"]) or (
                "JSON syntax and basic field checks passed; the structural "
                "validator still rejects this description. This is not a full "
                "schema compliance check."
            )
            issue["message"] = f"{issue['message']}: {detail}"


def validate_bids_dataset(dataset: str | Path) -> dict[str, Any]:
    report = get_bids_validation_report(dataset)
    if any(item.get("errors") != 0 for item in report["datasets"]):
        raise RuntimeError(f"Rust BIDS validation failed: {json.dumps(report)}")
    return report


def validate_and_repair_bids_dataset(
    dataset: str | Path,
    *,
    bids_version: str | None = None,
    suffix_aliases: dict[str, str] | None = None,
    severity_threshold: BidsSeverity | str = BidsSeverity.WARNING,
) -> dict[str, Any]:
    """Apply at most two safe repair passes, revalidate, and retain diagnostics."""
    from npdb.bids_repair import (
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
    aliases = _validate_aliases(
        suffix_aliases if suffix_aliases is not None else {}
    )
    try:
        original_report = get_bids_validation_report(root)
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
            current_report = get_bids_validation_report(root)
        except RuntimeError as exc:
            raise RuntimeError(
                f"BIDS repair was applied but revalidation failed; recovery information "
                f"is available at {journals[-1] if journals else 'the repair journal'}: {exc}"
            ) from exc

    original_keys = _report_issue_keys(original_report)
    final_keys = _report_issue_keys(current_report)
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
        current_report, unresolved_candidates, original_keys
    )
    resolved = [
        finding
        for finding in _normalised_report_issues(original_report)
        if _issue_key(finding) not in final_keys
    ]
    newly_introduced = [
        finding
        for finding in unresolved
        if _issue_key(finding) not in original_keys
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
    exceeding = findings_above_threshold(final_report, threshold)
    repair_summary["severity_gate"] = {
        "threshold": threshold.value,
        "passed": not exceeding,
        "exceeding": exceeding,
    }

    counts = _final_finding_counts(final_report)
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


def _final_finding_counts(report: dict[str, Any]) -> dict[str, int]:
    counts = {severity.value: 0 for severity in BidsSeverity}
    findings = {
        _issue_key(finding): finding
        for finding in [
            *_normalised_report_issues(report),
            *report["repair"]["unresolved"],
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
            counts[severity] += max(0, dataset_report.get(field, 0) - listed)
    return counts


def _remaining_findings(
    report: dict[str, Any],
    candidates: list[dict[str, Any]],
    original_keys: set[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    candidate_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    candidate_by_location: dict[tuple[str, str], dict[str, Any]] = {}
    for finding in candidates:
        key = (
            str(finding.get("code", "")),
            str(finding.get("path", "")),
            str(finding.get("message", "")),
        )
        candidate_index[key] = finding
        candidate_by_location[(key[0], key[1])] = finding
    remaining: list[dict[str, Any]] = []
    for dataset_report in report.get("datasets", []):
        for issue in dataset_report.get("issues", []):
            if not isinstance(issue, dict):
                continue
            key = (
                str(issue.get("code", "")),
                str(issue.get("path", "")),
                str(issue.get("message", "")),
            )
            previous = candidate_index.get(key) or candidate_by_location.get(
                (key[0], key[1])
            )
            finding = {
                "code": key[0] or "UNKNOWN_VALIDATOR_ISSUE",
                "severity": str(issue.get("severity", "info")).lower(),
                "message": key[2] or "Validator reported an issue.",
            }
            if key[1]:
                finding["path"] = key[1]
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
            remaining.append(finding)
    for finding in candidates:
        if finding.get("code") not in {
            "INVALID_DATASET_DESCRIPTION",
            "INVALID_FILENAME",
        }:
            remaining.append(finding)
    deduplicated = {
        (
            str(item.get("code", "")),
            str(item.get("path", "")),
            str(item.get("message", "")),
        ): item
        for item in remaining
    }
    return sorted(
        deduplicated.values(),
        key=lambda item: (
            str(item.get("path", "")),
            str(item.get("code", "")),
            str(item.get("message", "")),
        ),
    )


def _normalised_report_issues(report: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for dataset_report in report.get("datasets", []):
        for issue in dataset_report.get("issues", []):
            if not isinstance(issue, dict):
                continue
            finding = {
                "code": str(issue.get("code", "UNKNOWN_VALIDATOR_ISSUE")),
                "severity": str(issue.get("severity", "info")).lower(),
                "message": str(issue.get("message", "Validator reported an issue.")),
            }
            if issue.get("path") is not None:
                finding["path"] = str(issue["path"])
            findings.append(finding)
    return findings


def _report_issue_keys(report: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {_issue_key(item) for item in _normalised_report_issues(report)}


def _issue_key(finding: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(finding.get("code", "")),
        str(finding.get("path", "")),
        str(finding.get("message", "")),
    )


def _validator_executable() -> str:
    executable = shutil.which("bids-validator-rust")
    if executable is None:
        candidate = Path(sys.executable).parent / "bids-validator-rust"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            executable = str(candidate)
    if executable is None:
        raise RuntimeError(
            "The Rust BIDS validator is required. Run "
            "bash scripts/install_bids_validator.sh after installing Rust 1.85+."
        )
    return executable

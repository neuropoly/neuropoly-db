import json
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import Any

from npdb.external.bids_issues import repair_kind
from npdb.external.bids_version import resolve_bids_version
from npdb.external.native import validator_executable


class BidsSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class BidsValidationError(RuntimeError):
    """Final validation exceeded the configured threshold; retains the audit."""

    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = report


def _is_within_path(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _issue_dataset_root(
    finding: dict[str, Any],
    *,
    dataset_root: Path | None,
) -> Path | None:
    if dataset_root is None:
        return None
    raw_dataset = finding.get("dataset")
    if raw_dataset:
        owner = Path(str(raw_dataset))
        return owner if owner.is_absolute() else dataset_root / owner
    return None


def _path_candidate(
    finding: dict[str, Any],
    *,
    dataset_root: Path | None,
) -> Path | None:
    if dataset_root is None:
        return None
    raw_path = finding.get("path")
    if raw_path in (None, ""):
        return None
    path = Path(str(raw_path))
    return path if path.is_absolute() else dataset_root / path


def _is_derivative_finding(
    finding: dict[str, Any],
    *,
    dataset_root: Path | None,
) -> bool:
    if dataset_root is None:
        return False
    derivatives_root = dataset_root / "derivatives"
    owner = _issue_dataset_root(finding, dataset_root=dataset_root)
    if owner is not None:
        if _is_within_path(derivatives_root, owner):
            return True
        if owner == dataset_root and dataset_root.parent.name == "derivatives":
            return True
    path_candidate = _path_candidate(finding, dataset_root=dataset_root)
    return path_candidate is not None and _is_within_path(
        derivatives_root, path_candidate
    )


def _effective_severity(
    finding: dict[str, Any],
    *,
    dataset_root: Path | None,
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
    finding: dict[str, Any],
    *,
    dataset_root: Path | None,
) -> dict[str, Any]:
    adjusted = dict(finding)
    adjusted_severity = _effective_severity(
        finding,
        dataset_root=dataset_root,
    )
    if adjusted_severity.value != str(adjusted.get("severity", "")).lower():
        adjusted["severity"] = adjusted_severity.value
        adjusted["original_severity"] = finding.get("severity", "error")
    return adjusted


def _finding_key(finding: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(finding.get("dataset", "")),
        str(finding.get("code", "")),
        str(finding.get("path", "")),
        str(finding.get("message", "")),
    )


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
        dataset = str(dataset_report.get("dataset", ""))
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
                        "dataset": dataset,
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
        try:
            severity = _effective_severity(finding, dataset_root=dataset_root)
        except ValueError as exc:
            raise RuntimeError(
                f"Unknown BIDS finding severity: {finding.get('severity')!r}"
            ) from exc
        if ranks[severity] > ranks[threshold]:
            exceeding[_finding_key(finding)] = finding
    return list(exceeding.values())


def get_bids_validation_report(dataset: str | Path) -> dict[str, Any]:
    """Return the validator's structured report, including reports with findings."""
    root = Path(dataset).resolve()
    executable = validator_executable()
    try:
        result = subprocess.run(
            [executable, str(root), "--format", "json", "--recursive"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"Cannot execute the Rust BIDS validator: {exc}") from exc
    try:
        upstream = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        detail = result.stdout or result.stderr or "no output"
        raise RuntimeError(
            f"Rust BIDS validator did not return a structured report: {detail}"
        ) from exc
    report = _adapt_report(root, upstream)
    errors = sum(item["errors"] for item in report["datasets"])
    if result.returncode not in (0, 1) or bool(result.returncode) != bool(errors):
        raise RuntimeError(
            f"Rust BIDS validator returned inconsistent exit status {result.returncode} "
            f"for {errors} error(s): {result.stderr or result.stdout}"
        )
    _explain_description_findings(root, report)
    return report


def _adapt_report(root: Path, upstream: Any) -> dict[str, Any]:
    datasets: list[dict[str, Any]] = []

    def visit(owner: Path, payload: Any) -> None:
        if not isinstance(payload, dict) or not isinstance(payload.get("issues"), dict):
            raise RuntimeError("Rust BIDS validator returned an invalid report object.")
        collection = payload["issues"]
        issues = collection.get("issues")
        messages = collection.get("codeMessages")
        derivatives = payload.get("derivativesSummary", {})
        if (
            not isinstance(issues, list)
            or not isinstance(messages, dict)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in messages.items()
            )
            or not isinstance(derivatives, dict)
        ):
            raise RuntimeError(
                "Rust BIDS validator returned invalid issues or derivatives."
            )
        normalized = []
        for issue in issues:
            if (
                not isinstance(issue, dict)
                or not isinstance(issue.get("code"), str)
                or not issue["code"]
                or not isinstance(issue.get("severity"), str)
                or issue["severity"] not in {"error", "warning"}
                or not isinstance(issue.get("location", ""), str)
                or (
                    "issueMessage" in issue
                    and not isinstance(issue["issueMessage"], str)
                )
            ):
                raise RuntimeError(
                    f"Rust BIDS validator returned an invalid issue: {issue!r}"
                )
            location = issue.get("location", "")
            # Upstream locations start with '/', but are relative to the owning dataset.
            relative = Path(location.lstrip("/"))
            if "\\" in location or relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"Unsafe BIDS validator location: {location!r}")
            path = owner / relative
            if not path.resolve().is_relative_to(root):
                raise RuntimeError(
                    f"BIDS validator location escapes the dataset: {location!r}"
                )
            message = messages.get(issue["code"], "")
            detail = issue.get("issueMessage", "")
            normalized.append(
                {
                    **issue,
                    "message": ": ".join(part for part in (message, detail) if part)
                    or f"Validator reported {issue['code']}.",
                    "path": str(path),
                }
            )
        datasets.append(
            {
                "dataset": str(owner),
                "errors": sum(item["severity"] == "error" for item in normalized),
                "warnings": sum(item["severity"] == "warning" for item in normalized),
                "issues": normalized,
            }
        )
        for name, child in sorted(derivatives.items()):
            if (
                not isinstance(name, str)
                or name in {"", ".", ".."}
                or "/" in name
                or "\\" in name
            ):
                raise RuntimeError(f"Unsafe derivative dataset name: {name!r}")
            child_root = owner / "derivatives" / name
            if not child_root.resolve().is_relative_to(root):
                raise RuntimeError(
                    f"Derivative dataset escapes requested root: {name!r}"
                )
            visit(child_root, child)

    visit(root, upstream)
    return {
        "validator": "rordenlab/bids-validator-rs",
        "coverage": "Pinned Rust beta: filename, metadata, TSV and NIfTI checks; "
        "not certification of complete official-validator parity.",
        "datasets": datasets,
        "upstream_report": upstream,
    }


def _explain_description_findings(root: Path, report: dict[str, Any]) -> None:
    from npdb.bids_repair import _assert_safe_path, inspect_dataset_description

    root = root.resolve()
    for dataset_report in report["datasets"]:
        owner = Path(dataset_report.get("dataset", root))
        if not owner.is_absolute():
            owner = root / owner
        _assert_safe_path(root, owner, must_exist=True)
        for issue in dataset_report.get("issues", []):
            if repair_kind(issue) != "description":
                continue
            diagnostics = inspect_dataset_description(owner)
            issue["diagnostics"] = diagnostics
            detail = "; ".join(diagnostics["problems"]) or (
                "JSON syntax and basic field checks passed; the pinned native "
                "validator still rejects this description. This is not a full "
                "schema compliance check."
            )
            issue["message"] = f"{issue['message']}: {detail}"


def validate_bids_dataset(dataset: str | Path) -> dict[str, Any]:
    root = Path(dataset).resolve()
    report = get_bids_validation_report(root)
    if findings_above_threshold(report, BidsSeverity.WARNING, dataset_root=root):
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
    aliases = _validate_aliases(suffix_aliases if suffix_aliases is not None else {})
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
        current_report,
        unresolved_candidates,
        original_keys,
        dataset_root=root,
    )
    resolved = [
        finding
        for finding in _normalised_report_issues(original_report, dataset_root=root)
        if _finding_key(finding) not in final_keys
    ]
    newly_introduced = [
        finding for finding in unresolved if _finding_key(finding) not in original_keys
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
    report: dict[str, Any],
    *,
    dataset_root: Path | None = None,
) -> dict[str, int]:
    counts = {severity.value: 0 for severity in BidsSeverity}
    findings = {
        _finding_key(finding): finding
        for finding in [
            *_normalised_report_issues(report, dataset_root=dataset_root),
            *(
                _effective_finding(item, dataset_root=dataset_root)
                for item in report["repair"]["unresolved"]
            ),
        ]
    }
    for finding in findings.values():
        severity = _effective_severity(finding, dataset_root=dataset_root)
        counts[severity.value] += 1
    for dataset_report in report["datasets"]:
        dataset = str(dataset_report.get("dataset", ""))
        for severity, field in (("warning", "warnings"), ("error", "errors")):
            listed = sum(
                str(issue.get("severity", "")).lower() == severity
                for issue in dataset_report.get("issues", [])
                if isinstance(issue, dict)
            )
            remaining = max(0, dataset_report.get(field, 0) - listed)
            if remaining <= 0:
                continue
            finding = _effective_finding(
                {
                    "code": "UNLISTED_VALIDATOR_FINDINGS",
                    "severity": severity,
                    "dataset": dataset,
                    "path": dataset,
                    "message": (
                        f"Validator reported {remaining} additional "
                        f"{severity} finding(s)."
                    ),
                },
                dataset_root=dataset_root,
            )
            counts[str(finding["severity"]).lower()] += remaining
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
        dataset = str(finding.get("dataset", ""))
        key = (
            dataset,
            str(finding.get("code", "")),
            str(finding.get("path", "")),
            str(finding.get("message", "")),
        )
        candidate_index[key] = finding
        candidate_by_location[(dataset, key[1], key[2])] = finding
    remaining: list[dict[str, Any]] = []
    for dataset_report in report.get("datasets", []):
        dataset = str(dataset_report.get("dataset", ""))
        for issue in dataset_report.get("issues", []):
            if not isinstance(issue, dict):
                continue
            path = "" if issue.get("path") is None else str(issue.get("path"))
            key = (
                dataset,
                str(issue.get("code", "")),
                path,
                str(issue.get("message", "")),
            )
            previous = candidate_index.get(key) or candidate_by_location.get(
                (dataset, key[1], key[2])
            )
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
            elif _finding_key(finding) not in original_keys:
                finding["resolution"] = (
                    "New finding appeared during revalidation; no safe automatic repair is available."
                )
            else:
                finding["resolution"] = (
                    "The finding remains after the bounded automatic repair pass."
                )
            remaining.append(_effective_finding(finding, dataset_root=dataset_root))
    for finding in candidates:
        if repair_kind(finding) is None:
            remaining.append(_effective_finding(finding, dataset_root=dataset_root))
    deduplicated = {
        (
            str(item.get("dataset", "")),
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


def _normalised_report_issues(
    report: dict[str, Any],
    *,
    dataset_root: Path | None = None,
) -> list[dict[str, Any]]:
    findings = []
    for dataset_report in report.get("datasets", []):
        dataset = str(dataset_report.get("dataset", ""))
        for issue in dataset_report.get("issues", []):
            if not isinstance(issue, dict):
                continue
            finding = {
                "dataset": dataset,
                "code": str(issue.get("code", "UNKNOWN_VALIDATOR_ISSUE")),
                "severity": str(issue.get("severity", "info")).lower(),
                "message": str(issue.get("message", "Validator reported an issue.")),
            }
            if issue.get("path") is not None:
                finding["path"] = str(issue["path"])
            findings.append(_effective_finding(finding, dataset_root=dataset_root))
    return findings


def _report_issue_keys(
    report: dict[str, Any],
    *,
    dataset_root: Path | None = None,
) -> set[tuple[str, str, str, str]]:
    return {
        _finding_key(item)
        for item in _normalised_report_issues(report, dataset_root=dataset_root)
    }

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from bids_repair import (
    BidsRepairError,
    apply_bids_repair,
    plan_bids_repair,
    recover_bids_repair,
)
from bids_repair.validation import (
    BidsValidationError,
    findings_above_threshold,
    get_bids_validation_report,
    validate_and_repair_bids_dataset,
    validate_bids_dataset,
)


def _report(root: Path, *issues: dict) -> dict:
    errors = sum(issue.get("severity") == "error" for issue in issues)
    warnings = sum(issue.get("severity") == "warning" for issue in issues)
    return {
        "validator": "test",
        "coverage": "structural",
        "datasets": [
            {
                "dataset": str(root),
                "errors": errors,
                "warnings": warnings,
                "issues": list(issues),
            }
        ],
    }


def _multi_dataset_report(*datasets: dict) -> dict:
    return {
        "validator": "test",
        "coverage": "structural",
        "datasets": list(datasets),
    }


def _filename_issue(path: str, severity: str = "warning") -> dict:
    return {
        "code": "INVALID_FILENAME",
        "severity": severity,
        "message": "File does not match any BIDS naming pattern",
        "path": path,
    }


def _make_dataset(tmp_path: Path) -> Path:
    root = tmp_path / "dataset"
    root.mkdir()
    return root


def test_plan_normalizes_description_losslessly_with_explicit_version(tmp_path):
    root = _make_dataset(tmp_path)
    description = root / "dataset_description.json"
    description.write_text(
        json.dumps(
            {
                "Name": "Example",
                "Authors": "One author",
                "DatasetDOI": "doi:example",
                "CustomField": {"retained": True},
            }
        ),
        encoding="utf-8",
    )
    report = _report(
        root,
        {
            "code": "INVALID_DATASET_DESCRIPTION",
            "severity": "error",
            "message": "dataset_description.json cannot be parsed",
            "path": "dataset_description.json",
        },
    )

    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        plan = plan_bids_repair(root, bids_version="1.9.0")

    assert len(plan["operations"]) == 1
    assert json.loads(description.read_text())["Authors"] == "One author"
    updated = json.loads(plan["operations"][0]["content"])
    assert updated == {
        "Name": "Example",
        "Authors": ["One author"],
        "DatasetDOI": "doi:example",
        "CustomField": {"retained": True},
        "BIDSVersion": "1.9.0",
    }
    assert plan["blocking"] is False


def test_description_plan_keeps_authors_fix_and_reports_missing_version(tmp_path):
    root = _make_dataset(tmp_path)
    (root / "dataset_description.json").write_text(
        '{"Name":"Example","Authors":"One author"}', encoding="utf-8"
    )
    report = _report(
        root,
        {
            "code": "INVALID_DATASET_DESCRIPTION",
            "severity": "error",
            "message": "description invalid",
            "path": "dataset_description.json",
        },
    )

    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        plan = plan_bids_repair(root)

    assert len(plan["operations"]) == 1
    assert plan["blocking"] is True
    assert any(item["code"] == "BIDS_VERSION_REQUIRED" for item in plan["findings"])


def test_description_plan_distinguishes_malformed_json_and_schema_type(tmp_path):
    root = _make_dataset(tmp_path)
    description = root / "dataset_description.json"
    report = _report(
        root,
        {
            "code": "INVALID_DATASET_DESCRIPTION",
            "severity": "error",
            "message": "description invalid",
            "path": "dataset_description.json",
        },
    )
    description.write_text("{", encoding="utf-8")

    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        malformed = plan_bids_repair(root)

    assert any(
        item["code"] == "INVALID_DATASET_DESCRIPTION_JSON"
        for item in malformed["findings"]
    )
    assert malformed["operations"] == []

    description.write_text('{"Name":"Example","Authors":4}', encoding="utf-8")

    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        wrong_type = plan_bids_repair(root)

    assert any(
        item["code"] == "INVALID_DATASET_DESCRIPTION_SCHEMA"
        for item in wrong_type["findings"]
    )
    assert wrong_type["operations"] == []


def test_explicit_version_is_applied_without_overwriting_unsupported_authors(tmp_path):
    root = _make_dataset(tmp_path)
    description = root / "dataset_description.json"
    description.write_text('{"Name":"Example","Authors":4}', encoding="utf-8")
    report = _report(
        root,
        {
            "code": "INVALID_DATASET_DESCRIPTION",
            "severity": "error",
            "message": "description invalid",
            "path": "dataset_description.json",
        },
    )

    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        plan = plan_bids_repair(root, bids_version="1.9.0")

    updated = json.loads(plan["operations"][0]["content"])
    assert updated["BIDSVersion"] == "1.9.0"
    assert updated["Authors"] == 4
    assert any(
        item["code"] == "INVALID_DATASET_DESCRIPTION_SCHEMA"
        for item in plan["findings"]
    )
    assert plan["blocking"] is True


def test_plan_infers_subject_session_orders_entities_and_normalizes_run(tmp_path):
    root = _make_dataset(tmp_path)
    directory = root / "sub-01" / "ses-02" / "func"
    directory.mkdir(parents=True)
    source = directory / "run2_task-rest_bold.nii.gz"
    source.write_bytes(b"nifti fixture")
    report = _report(
        root,
        _filename_issue("sub-01/ses-02/func/run2_task-rest_bold.nii.gz"),
    )

    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        plan = plan_bids_repair(root)

    rename = next(item for item in plan["operations"] if item["kind"] == "rename")
    assert Path(rename["target"]).name == "sub-01_ses-02_task-rest_run-2_bold.nii.gz"
    assert source.exists()


def test_ambiguous_tokens_and_conflicting_directory_entities_stay_unresolved(tmp_path):
    root = _make_dataset(tmp_path)
    directory = root / "sub-01" / "func"
    directory.mkdir(parents=True)
    (directory / "sub-02_task-rest_bold.nii.gz").write_bytes(b"x")
    (directory / "sub-01_task-rest_WE_events.tsv").write_text("x", encoding="utf-8")
    report = _report(
        root,
        _filename_issue("sub-01/func/sub-02_task-rest_bold.nii.gz", "error"),
        _filename_issue("sub-01/func/sub-01_task-rest_WE_events.tsv"),
    )
    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        plan = plan_bids_repair(root)
    assert plan["operations"] == []
    assert any("conflicts" in item.get("resolution", "") for item in plan["findings"])
    assert any(
        "Unrecognized filename token" in item.get("resolution", "")
        for item in plan["findings"]
    )
    assert plan["blocking"] is True


def test_explicit_suffix_alias_is_applied_and_alias_chains_are_rejected(tmp_path):
    root = _make_dataset(tmp_path)
    path = root / "sub-01" / "func" / "sub-01_task-rest_oldbold.nii.gz"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    report = _report(
        root, _filename_issue("sub-01/func/sub-01_task-rest_oldbold.nii.gz")
    )
    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        plan = plan_bids_repair(root, suffix_aliases={"oldbold": "bold"})
    rename = next(item for item in plan["operations"] if item["kind"] == "rename")
    assert Path(rename["target"]).name == "sub-01_task-rest_bold.nii.gz"
    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        many_to_one = plan_bids_repair(
            root, suffix_aliases={"oldbold": "bold", "otherbold": "bold"}
        )
    assert many_to_one["suffix_aliases"] == {
        "oldbold": "bold",
        "otherbold": "bold",
    }
    with pytest.raises(ValueError, match="Conflicting suffix aliases"):
        with patch(
            "bids_repair.validation.get_bids_validation_report", return_value=report
        ):
            plan_bids_repair(
                root,
                suffix_aliases={"oldbold": "oldevents", "oldevents": "bold"},
            )


def test_apply_updates_associated_files_and_intendedfor_with_metadata_backup(tmp_path):
    root = _make_dataset(tmp_path)
    func = root / "sub-01" / "func"
    fmap = root / "sub-01" / "fmap"
    func.mkdir(parents=True)
    fmap.mkdir(parents=True)
    stem = "sub-01_task-rest_run2_bold"
    (func / f"{stem}.nii.gz").write_bytes(b"image")
    sidecar = func / f"{stem}.json"
    sidecar.write_text('{"RepetitionTime":2}', encoding="utf-8")
    reference = fmap / "sub-01_phasediff.json"
    reference.write_text(
        json.dumps({"IntendedFor": [f"sub-01/func/{stem}.nii.gz"]}),
        encoding="utf-8",
    )
    report = _report(
        root,
        _filename_issue(f"sub-01/func/{stem}.nii.gz"),
    )
    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        plan = plan_bids_repair(root)

    result = apply_bids_repair(plan)
    new_stem = "sub-01_task-rest_run-2_bold"
    assert (func / f"{new_stem}.nii.gz").read_bytes() == b"image"
    assert json.loads((func / f"{new_stem}.json").read_text())["RepetitionTime"] == 2
    assert json.loads(reference.read_text())["IntendedFor"] == [
        f"sub-01/func/{new_stem}.nii.gz"
    ]
    assert result["applied"]
    assert result["journal"]
    assert result["backups"]
    assert all(Path(backup).is_file() for backup in result["backups"])
    assert recover_bids_repair(root)["recovered"] is False


def test_application_rejects_stale_plan_collisions_and_symlinks(tmp_path):
    root = _make_dataset(tmp_path)
    directory = root / "sub-01" / "func"
    directory.mkdir(parents=True)
    source = directory / "sub-01_task-rest_run2_bold.nii.gz"
    source.write_bytes(b"original")
    report = _report(
        root, _filename_issue("sub-01/func/sub-01_task-rest_run2_bold.nii.gz")
    )
    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        plan = plan_bids_repair(root)
    source.write_bytes(b"changed")
    with pytest.raises(BidsRepairError, match="stale"):
        apply_bids_repair(plan)

    source.write_bytes(b"original")
    target = directory / "sub-01_task-rest_run-2_bold.nii.gz"
    target.write_bytes(b"unrelated")
    with pytest.raises(BidsRepairError, match="overwrite"):
        apply_bids_repair(plan)

    target.unlink()
    source.unlink()
    outside = tmp_path / "outside.nii.gz"
    outside.write_bytes(b"outside")
    source.symlink_to(outside)
    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        symlink_plan = plan_bids_repair(root)
    assert symlink_plan["operations"] == []


def test_failed_application_leaves_journal_recoverable(tmp_path):
    root = _make_dataset(tmp_path)
    directory = root / "sub-01" / "func"
    directory.mkdir(parents=True)
    filenames = [
        "sub-01_task-rest_run2_bold.nii.gz",
        "sub-01_task-rest_run3_bold.nii.gz",
    ]
    for filename in filenames:
        (directory / filename).write_bytes(filename.encode())
    report = _report(
        root,
        *[_filename_issue(f"sub-01/func/{filename}") for filename in filenames],
    )
    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        plan = plan_bids_repair(root)

    from bids_repair import repair

    original_rename = repair._rename_no_replace
    calls = 0

    def fail_second(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected failure")
        original_rename(source, target)

    with (
        patch("bids_repair.repair._rename_no_replace", side_effect=fail_second),
        patch(
            "bids_repair.repair._recover_journal", side_effect=OSError("defer recovery")
        ),
        pytest.raises(BidsRepairError, match="rollback also failed"),
    ):
        apply_bids_repair(plan)

    recovered = recover_bids_repair(root)
    assert recovered["recovered"] is True
    for filename in filenames:
        assert (directory / filename).is_file()
        assert not (
            directory / filename.replace("run2", "run-2").replace("run3", "run-3")
        ).exists()


def test_validator_report_survives_nonzero_exit_but_standalone_validator_stays_strict(
    tmp_path,
):
    report = _report(
        tmp_path,
        {
            "code": "INVALID_DATASET_DESCRIPTION",
            "severity": "error",
            "message": "invalid description",
        },
    )
    with (
        patch("bids_repair.validation._validator_executable", return_value="validator"),
        patch(
            "bids_repair.validation._native_dataset_report",
            return_value=report["datasets"][0],
        ),
    ):
        inspected = get_bids_validation_report(tmp_path)
        assert inspected["datasets"][0]["errors"] == 1
        issue = inspected["datasets"][0]["issues"][0]
        assert issue["code"] == "INVALID_DATASET_DESCRIPTION"
        assert issue["diagnostics"]["json_valid"] is False
        assert "missing or unsafe" in issue["message"]
    with (
        patch("bids_repair.validation._validator_executable", return_value="validator"),
        patch(
            "bids_repair.validation._native_dataset_report",
            return_value=report["datasets"][0],
        ),
        pytest.raises(RuntimeError, match="validation failed"),
    ):
        validate_bids_dataset(tmp_path)


def test_orchestrator_revalidates_and_fails_with_unresolved_errors(tmp_path):
    root = _make_dataset(tmp_path)
    file_path = root / "sub-01" / "func" / "sub-01_task-rest_run2_bold.nii.gz"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"image")
    original = _report(
        root,
        _filename_issue("sub-01/func/sub-01_task-rest_run2_bold.nii.gz", "error"),
    )
    final = _report(
        root,
        {
            "code": "UNSUPPORTED",
            "severity": "error",
            "message": "unresolved",
            "path": "sub-01/func/file",
        },
    )
    with (
        patch(
            "bids_repair.validation.get_bids_validation_report",
            side_effect=[original, final],
        ),
        pytest.raises(BidsValidationError, match="above 'warning'"),
    ):
        validate_and_repair_bids_dataset(root)
    assert not file_path.exists()
    assert file_path.with_name("sub-01_task-rest_run-2_bold.nii.gz").exists()


@pytest.mark.parametrize(
    "severity,threshold,passes",
    [
        ("info", "info", True),
        ("warning", "info", False),
        ("error", "info", False),
        ("info", "warning", True),
        ("warning", "warning", True),
        ("error", "warning", False),
        ("info", "error", True),
        ("warning", "error", True),
        ("error", "error", True),
    ],
)
def test_final_severity_threshold_is_exclusive(tmp_path, severity, threshold, passes):
    root = _make_dataset(tmp_path)
    report = _report(
        root,
        {"code": "UNSUPPORTED", "severity": severity, "message": "Cannot repair"},
    )
    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        if passes:
            result = validate_and_repair_bids_dataset(
                root, severity_threshold=threshold
            )
        else:
            with pytest.raises(BidsValidationError) as failure:
                validate_and_repair_bids_dataset(root, severity_threshold=threshold)
            result = failure.value.report
    gate = result["repair"]["severity_gate"]
    assert gate["threshold"] == threshold
    assert gate["passed"] is passes
    assert bool(gate["exceeding"]) is not passes
    assert result["datasets"] == report["datasets"]
    assert result["repair"]["unresolved"][0]["severity"] == severity


def test_derivative_errors_are_downgraded_to_warnings_by_default(tmp_path):
    root = _make_dataset(tmp_path)
    derivatives = root / "derivatives" / "labels"
    derivatives.mkdir(parents=True)
    report = _multi_dataset_report(
        {"dataset": str(root), "errors": 0, "warnings": 0, "issues": []},
        {
            "dataset": str(derivatives),
            "errors": 1,
            "warnings": 0,
            "issues": [
                {
                    "code": "MISSING_DATASET_DESCRIPTION",
                    "severity": "error",
                    "message": "dataset_description.json is required at the root",
                }
            ],
        },
    )
    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        result = validate_and_repair_bids_dataset(root)
    assert result["repair"]["counts"]["error"] == 0
    assert result["repair"]["counts"]["warning"] == 1
    unresolved = result["repair"]["unresolved"][0]
    assert unresolved["severity"] == "warning"
    assert unresolved["original_severity"] == "error"


def test_derivative_warnings_still_fail_at_info_threshold(tmp_path):
    root = _make_dataset(tmp_path)
    report = _multi_dataset_report(
        {"dataset": str(root), "errors": 0, "warnings": 0, "issues": []},
        {
            "dataset": str(root / "derivatives" / "labels"),
            "errors": 1,
            "warnings": 0,
            "issues": [
                {
                    "code": "DERIVATIVE_PROVENANCE",
                    "severity": "error",
                    "message": "Missing dataset_description.json in project root",
                }
            ],
        },
    )
    with (
        patch("bids_repair.validation.get_bids_validation_report", return_value=report),
        pytest.raises(BidsValidationError, match="above 'info'"),
    ):
        validate_and_repair_bids_dataset(root, severity_threshold="info")


def test_identical_findings_keep_their_owning_dataset(tmp_path):
    root = _make_dataset(tmp_path)
    owners = [root, root / "derivatives" / "first", root / "derivatives" / "second"]
    issue = {
        "code": "UNSUPPORTED",
        "severity": "error",
        "path": "sub-01/anat/sub-01_T1w.nii.gz",
        "message": "Cannot repair",
    }
    report = _multi_dataset_report(
        *(
            {"dataset": str(owner), "errors": 1, "warnings": 0, "issues": [issue]}
            for owner in owners
        )
    )

    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        result = validate_and_repair_bids_dataset(root, severity_threshold="error")

    summary = result["repair"]
    assert summary["counts"] == {"info": 0, "warning": 2, "error": 1}
    assert {finding["dataset"] for finding in summary["unresolved"]} == {
        str(owner) for owner in owners
    }
    assert summary["resolved"] == summary["newly_introduced"] == []
    assert result["datasets"] == report["datasets"]
    assert all(item["issues"][0]["severity"] == "error" for item in report["datasets"])
    exceeding = findings_above_threshold(result, dataset_root=root)
    assert len(exceeding) == 1
    assert exceeding[0]["dataset"] == str(root)


@pytest.mark.parametrize("threshold,passes", [("warning", True), ("info", False)])
def test_unlisted_derivative_findings_use_effective_severity(
    tmp_path, threshold, passes
):
    root = _make_dataset(tmp_path)
    report = _multi_dataset_report(
        {"dataset": str(root), "errors": 0, "warnings": 0, "issues": []},
        {
            "dataset": str(root / "derivatives" / "labels"),
            "errors": 3,
            "warnings": 2,
            "issues": [],
        },
    )

    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        if passes:
            result = validate_and_repair_bids_dataset(
                root, severity_threshold=threshold
            )
        else:
            with pytest.raises(BidsValidationError) as failure:
                validate_and_repair_bids_dataset(root, severity_threshold=threshold)
            result = failure.value.report

    assert result["repair"]["counts"] == {"info": 0, "warning": 5, "error": 0}
    gate = result["repair"]["severity_gate"]
    assert gate["passed"] is passes
    assert all(finding["severity"] == "warning" for finding in gate["exceeding"])
    assert result["datasets"] == report["datasets"]


def test_standalone_validation_ignores_derivative_only_errors(tmp_path):
    root = _make_dataset(tmp_path)
    report = _multi_dataset_report(
        {"dataset": str(root), "errors": 0, "warnings": 0, "issues": []},
        {
            "dataset": str(root / "derivatives" / "labels"),
            "errors": 1,
            "warnings": 0,
            "issues": [
                {
                    "code": "MISSING_DATASET_DESCRIPTION",
                    "severity": "error",
                    "message": "dataset_description.json is required at the root",
                }
            ],
        },
    )
    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        assert validate_bids_dataset(root) == report


def test_revalidation_does_not_mark_same_pathless_derivative_issue_as_new(tmp_path):
    root = _make_dataset(tmp_path)
    deriv_root = root / "derivatives" / "labels"
    deriv_root.mkdir(parents=True)
    file_path = root / "sub-01" / "func" / "sub-01_task-rest_run2_bold.nii.gz"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"image")
    derivative_issue = {
        "code": "DERIVATIVE_PROVENANCE",
        "severity": "error",
        "message": "Missing dataset_description.json in project root",
    }
    initial = _multi_dataset_report(
        {
            "dataset": str(root),
            "errors": 1,
            "warnings": 0,
            "issues": [
                _filename_issue(
                    "sub-01/func/sub-01_task-rest_run2_bold.nii.gz", "error"
                )
            ],
        },
        {
            "dataset": str(deriv_root),
            "errors": 1,
            "warnings": 0,
            "issues": [{**derivative_issue, "path": None}],
        },
    )
    final = _multi_dataset_report(
        {"dataset": str(root), "errors": 0, "warnings": 0, "issues": []},
        {
            "dataset": str(deriv_root),
            "errors": 1,
            "warnings": 0,
            "issues": [derivative_issue],
        },
    )
    with patch(
        "bids_repair.validation.get_bids_validation_report",
        side_effect=[initial, final],
    ):
        result = validate_and_repair_bids_dataset(root)
    assert result["repair"]["newly_introduced"] == []
    unresolved = result["repair"]["unresolved"][0]
    assert "New finding appeared during revalidation" not in unresolved["resolution"]
    assert unresolved["severity"] == "warning"


def test_threshold_is_not_checked_at_entry_or_between_repair_passes(tmp_path):
    root = _make_dataset(tmp_path)
    directory = root / "sub-01" / "func"
    directory.mkdir(parents=True)
    first = directory / "sub-01_task-rest_run2_bold.nii.gz"
    second = directory / "sub-01_task-rest_run3_bold.nii.gz"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    reports = [
        _report(
            root,
            _filename_issue(str(first.relative_to(root)), "error"),
            {"code": "INTERMEDIATE", "severity": "error", "message": "Transient"},
        ),
        _report(root, _filename_issue(str(second.relative_to(root)), "error")),
        _report(root),
    ]
    with patch(
        "bids_repair.validation.get_bids_validation_report", side_effect=reports
    ) as validate:
        result = validate_and_repair_bids_dataset(root, severity_threshold="info")
    assert validate.call_count == 3
    assert first.with_name("sub-01_task-rest_run-2_bold.nii.gz").exists()
    assert second.with_name("sub-01_task-rest_run-3_bold.nii.gz").exists()
    assert result["repair"]["unresolved"] == []
    assert result["repair"]["severity_gate"]["passed"] is True
    assert len(result["repair"]["journals"]) == 2


def test_permissive_threshold_still_repairs_and_reports_remaining_errors(tmp_path):
    root = _make_dataset(tmp_path)
    description = root / "dataset_description.json"
    description.write_text('{"Name":"Example","Authors":"One author"}')
    report = _report(
        root,
        {
            "code": "INVALID_DATASET_DESCRIPTION",
            "severity": "error",
            "message": "Invalid description",
        },
    )
    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        result = validate_and_repair_bids_dataset(root, severity_threshold="error")
    assert json.loads(description.read_text())["Authors"] == ["One author"]
    assert result["repair"]["applied"]
    assert any(
        item["code"] == "BIDS_VERSION_REQUIRED"
        for item in result["repair"]["unresolved"]
    )
    assert result["repair"]["severity_gate"]["passed"]


def test_permissive_threshold_does_not_hide_application_failure(tmp_path):
    root = _make_dataset(tmp_path)
    (root / "dataset_description.json").write_text(
        '{"Name":"Example","BIDSVersion":"1.9.0","Authors":"One author"}'
    )
    report = _report(
        root,
        {
            "code": "INVALID_DATASET_DESCRIPTION",
            "severity": "error",
            "message": "Invalid",
        },
    )
    with (
        patch("bids_repair.validation.get_bids_validation_report", return_value=report),
        patch(
            "bids_repair.repair.apply_bids_repair",
            side_effect=BidsRepairError("unsafe"),
        ),
        pytest.raises(RuntimeError, match="application failed: unsafe"),
    ):
        validate_and_repair_bids_dataset(root, severity_threshold="error")


def test_threshold_does_not_ignore_counts_without_issue_details(tmp_path):
    report = {"datasets": [{"dataset": str(tmp_path), "errors": 1, "warnings": 2}]}
    assert len(findings_above_threshold(report, "warning")) == 1
    assert len(findings_above_threshold(report, "info")) == 2
    assert findings_above_threshold(report, "error") == []


def test_unknown_severity_and_invalid_threshold_fail_explicitly(tmp_path):
    report = _report(
        tmp_path, {"code": "UNKNOWN", "severity": "unexpected", "message": "Unknown"}
    )
    with pytest.raises(RuntimeError, match="Unknown BIDS finding severity"):
        findings_above_threshold(report, "error")
    with pytest.raises(ValueError):
        validate_and_repair_bids_dataset(tmp_path, severity_threshold="invalid")


def test_orchestrator_returns_final_report_with_repair_audit(tmp_path):
    root = _make_dataset(tmp_path)
    file_path = root / "sub-01" / "func" / "sub-01_task-rest_run2_bold.nii.gz"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"image")
    initial = _report(
        root, _filename_issue("sub-01/func/sub-01_task-rest_run2_bold.nii.gz")
    )
    final = _report(root)
    with patch(
        "bids_repair.validation.get_bids_validation_report",
        side_effect=[initial, final],
    ):
        result = validate_and_repair_bids_dataset(root)
    assert result["datasets"][0]["errors"] == 0
    assert result["repair"]["applied"][0]["code"] == "REPAIRED_FILENAME"
    assert result["repair"]["original_report"] == initial
    assert result["repair"]["final_report"] == final
    assert result["repair"]["journal"]


def test_warning_only_output_is_concise_and_manifest_retains_details(tmp_path, capsys):
    root = _make_dataset(tmp_path)
    path = root / "sub-01" / "func" / "sub-01_task-rest_UNKNOWN_events.tsv"
    path.parent.mkdir(parents=True)
    path.write_text("onset\tduration\n0\t1\n", encoding="utf-8")
    report = _report(
        root, _filename_issue("sub-01/func/sub-01_task-rest_UNKNOWN_events.tsv")
    )
    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        result = validate_and_repair_bids_dataset(root)

    output = capsys.readouterr().err
    assert result["datasets"][0]["errors"] == 0
    assert "info=0, warning=1, error=0" in output
    assert "INVALID_FILENAME" not in output
    assert str(path) not in output
    manifest_path = Path(result["repair"]["manifest"])
    assert str(manifest_path) in output
    saved = json.loads(manifest_path.read_text())
    assert saved == result
    finding = saved["repair"]["unresolved"][0]
    assert finding["path"] == "sub-01/func/sub-01_task-rest_UNKNOWN_events.tsv"
    assert "Unrecognized filename token" in finding["resolution"]
    assert saved["repair"]["journal"] is None


def test_failed_final_gate_persists_manifest_beside_journal(tmp_path, capsys):
    root = _make_dataset(tmp_path)
    (root / "dataset_description.json").write_text(
        '{"Name":"Example","Authors":"Author"}'
    )
    report = _report(
        root,
        {
            "code": "INVALID_DATASET_DESCRIPTION",
            "severity": "error",
            "message": "Description cannot be parsed",
        },
    )
    with (
        patch("bids_repair.validation.get_bids_validation_report", return_value=report),
        pytest.raises(BidsValidationError) as failure,
    ):
        validate_and_repair_bids_dataset(root)
    summary = failure.value.report["repair"]
    manifest = Path(summary["manifest"])
    assert manifest.parent == Path(summary["journal"]).parent
    saved = json.loads(manifest.read_text())
    assert saved["repair"]["severity_gate"]["passed"] is False
    assert saved["repair"]["applied"]
    assert saved["repair"]["original_report"] == report
    assert saved["repair"]["final_report"] == report
    assert any(
        finding["code"] == "BIDS_VERSION_REQUIRED"
        for finding in saved["repair"]["unresolved"]
    )
    assert "Description cannot be parsed" not in str(failure.value)
    assert str(manifest) in str(failure.value)
    assert len(capsys.readouterr().err.splitlines()) == 2


def test_manifest_counts_and_repeated_noop_runs_are_preserved(tmp_path, capsys):
    root = _make_dataset(tmp_path)
    report = _report(
        root,
        {"code": "INFO", "severity": "info", "message": "Information"},
        {"code": "WARN", "severity": "warning", "message": "Warning"},
        {"code": "ERR", "severity": "error", "message": "Error"},
    )
    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        first = validate_and_repair_bids_dataset(root, severity_threshold="error")
        second = validate_and_repair_bids_dataset(root, severity_threshold="error")
    assert first["repair"]["counts"] == {"info": 1, "warning": 1, "error": 1}
    assert first["repair"]["manifest"] != second["repair"]["manifest"]
    assert Path(first["repair"]["manifest"]).is_file()
    assert Path(second["repair"]["manifest"]).is_file()
    assert "info=1, warning=1, error=1" in capsys.readouterr().err
    assert recover_bids_repair(root)["recovered"] is False


def test_manifest_write_failure_is_explicit_and_not_success(tmp_path):
    root = _make_dataset(tmp_path)
    with (
        patch(
            "bids_repair.validation.get_bids_validation_report",
            return_value=_report(root),
        ),
        patch("bids_repair.repair._write_journal", side_effect=OSError("disk full")),
        pytest.raises(
            BidsRepairError, match="Cannot save BIDS validation manifest.*disk full"
        ),
    ):
        validate_and_repair_bids_dataset(root)


def test_manifest_refuses_symlink_output_directory(tmp_path):
    root = _make_dataset(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root.parent / f".{root.name}.npdb-bids-repair").symlink_to(
        outside, target_is_directory=True
    )
    with (
        patch(
            "bids_repair.validation.get_bids_validation_report",
            return_value=_report(root),
        ),
        pytest.raises(BidsRepairError, match="symlink manifest directory"),
    ):
        validate_and_repair_bids_dataset(root)
    assert list(outside.iterdir()) == []


def _run_real_validator(root: Path) -> dict:
    return get_bids_validation_report(root)


def _make_validator_dataset(root: Path, *, version: bool = True) -> None:
    root.mkdir()
    description = {"Name": "Repair validator fixture", "Authors": "One author"}
    if version:
        description["BIDSVersion"] = "1.9.0"
    (root / "dataset_description.json").write_text(
        json.dumps(description), encoding="utf-8"
    )
    (root / "participants.tsv").write_text("participant_id\nsub-01\n", encoding="utf-8")
    (root / "sub-01").mkdir()


def _write_invalid_run_file(root: Path) -> Path:
    func = root / "sub-01" / "func"
    func.mkdir(exist_ok=True)
    path = func / "sub-01_task-rest_run2_bold.nii.gz"
    import nibabel as nib
    import numpy as np

    image = nib.Nifti1Image(np.zeros((2, 2, 2, 2), dtype=np.float32), np.eye(4))
    image.header.set_zooms((1, 1, 1, 2))
    nib.save(image, path)
    path.with_name(path.name.removesuffix(".nii.gz") + ".json").write_text(
        json.dumps({"TaskName": "rest", "RepetitionTime": 2})
    )
    return path


@pytest.mark.parametrize(
    "repair_kind",
    ["description", "filename", "combined"],
)
def test_real_validator_confirms_description_and_filename_repairs(
    tmp_path, repair_kind
):
    root = tmp_path / f"real-{repair_kind}"
    _make_validator_dataset(root, version=repair_kind != "description")
    invalid_path = (
        _write_invalid_run_file(root) if repair_kind != "description" else None
    )

    original_report = _run_real_validator(root)
    from bids_repair.repair import issue_repair_kind

    original_issues = original_report["datasets"][0]["issues"]
    if repair_kind == "description":
        assert any(issue_repair_kind(item) == "description" for item in original_issues)
    elif repair_kind == "filename":
        assert any(
            issue_repair_kind(item) == "filename" and "run2_bold" in item["path"]
            for item in original_issues
        )
    else:
        assert any(issue_repair_kind(item) == "description" for item in original_issues)
        assert any(
            issue_repair_kind(item) == "filename" and "run2_bold" in item["path"]
            for item in original_issues
        )

    result = validate_and_repair_bids_dataset(
        root,
        bids_version="1.9.0" if repair_kind in {"description", "combined"} else None,
    )
    final_issues = result["datasets"][0]["issues"]
    assert not any(
        issue["code"] == "INVALID_DATASET_DESCRIPTION" for issue in final_issues
    )
    assert not any(
        issue["code"] == "INVALID_FILENAME" and "run2_bold" in issue.get("path", "")
        for issue in final_issues
    )
    if invalid_path is not None:
        assert invalid_path.with_name("sub-01_task-rest_run-2_bold.nii.gz").is_file()


def _type_issue(path: str) -> dict:
    return {
        "code": "TSV_VALUE_INCORRECT_TYPE",
        "severity": "error",
        "message": "A value in a column did not match the acceptable type: 'N/A'",
        "path": path,
    }


def test_missing_value_case_variants_are_normalized_once_per_table(tmp_path):
    root = _make_dataset(tmp_path)
    participants = root / "participants.tsv"
    participants.write_bytes(
        b"participant_id\tage\tsex\tnote\r\n"
        b"sub-01\tN/A\tN/a\tN/A text\r\n"
        b"sub-02\t30\tF\tNA\r\n"
    )
    report = _report(
        root, _type_issue(str(participants)), _type_issue(str(participants))
    )

    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        plan = plan_bids_repair(root)

    assert plan["blocking"] is False
    assert len(plan["operations"]) == 1
    operation = plan["operations"][0]
    assert operation["kind"] == "write"
    assert len(operation["original_findings"]) == 2
    assert operation["content"] == (
        "participant_id\tage\tsex\tnote\r\n"
        "sub-01\tn/a\tn/a\tN/A text\r\n"
        "sub-02\t30\tF\tNA\r\n"
    )

    result = apply_bids_repair(plan)

    assert participants.read_bytes() == operation["content"].encode("utf-8")
    assert result["applied"][0]["code"] == "REPAIRED_MISSING_VALUES"
    assert len(result["backups"]) == 1


def test_wrong_type_values_that_are_not_missing_markers_stay_blocking(tmp_path):
    root = _make_dataset(tmp_path)
    participants = root / "participants.tsv"
    participants.write_text("participant_id\tage\nsub-01\tunknown\n", encoding="utf-8")
    report = _report(root, _type_issue(str(participants)))

    with patch(
        "bids_repair.validation.get_bids_validation_report", return_value=report
    ):
        plan = plan_bids_repair(root)

    assert plan["operations"] == []
    assert plan["blocking"] is True
    assert "user correction" in plan["findings"][0]["resolution"]


def test_real_validator_accepts_normalized_missing_values(tmp_path):
    root = tmp_path / "real-missing"
    _make_validator_dataset(root)
    (root / "participants.tsv").write_text(
        "participant_id\tage\tsex\nsub-01\tN/A\tN/A\n", encoding="utf-8"
    )

    original = _run_real_validator(root)["datasets"][0]["issues"]
    assert any(item["code"] == "TSV_VALUE_INCORRECT_TYPE" for item in original)

    result = validate_and_repair_bids_dataset(root, bids_version="1.9.0")

    assert not any(
        issue["code"] == "TSV_VALUE_INCORRECT_TYPE"
        for issue in result["datasets"][0]["issues"]
    )
    assert (root / "participants.tsv").read_text(encoding="utf-8") == (
        "participant_id\tage\tsex\nsub-01\tn/a\tn/a\n"
    )

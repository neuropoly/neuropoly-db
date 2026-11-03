import json
from unittest.mock import Mock, patch

import pytest
from typer.testing import CliRunner

from npdb.cli.cli import local2bagel, npdb

runner = CliRunner()


def test_repair_bids_defaults_to_read_only_json_plan(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    plan = {
        "dataset": str(dataset),
        "bids_version": "1.9.0",
        "suffix_aliases": {"FA": "T1w", "RARE": "T1w"},
        "findings": [],
        "operations": [],
        "blocking": False,
    }
    with (
        patch("npdb.bids_repair.plan_bids_repair", return_value=plan) as planner,
        patch("npdb.bids_repair.apply_bids_repair") as apply,
    ):
        result = runner.invoke(
            npdb,
            [
                "repair",
                "bids",
                str(dataset),
                "--format",
                "json",
                "--bids-version",
                "1.9.0",
                "--suffix-alias",
                "FA=T1w",
                "--suffix-alias",
                "RARE=T1w",
            ],
        )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == plan
    planner.assert_called_once_with(
        dataset,
        bids_version="1.9.0",
        suffix_aliases={"FA": "T1w", "RARE": "T1w"},
    )
    apply.assert_not_called()


def test_repair_bids_apply_is_explicit_and_returns_application_result(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    application = {
        "applied": [{"code": "REPAIRED"}],
        "operations": [],
        "unresolved": [],
        "journal": None,
        "backups": [],
        "severity_gate": {"threshold": "warning", "passed": True, "exceeding": []},
    }
    with (
        patch("npdb.bids_repair.plan_bids_repair") as planner,
        patch(
            "npdb.external.bids.validate_and_repair_bids_dataset",
            return_value={"repair": application},
        ) as apply,
    ):
        result = runner.invoke(
            npdb, ["repair", "bids", str(dataset), "--apply", "--format", "json"]
        )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["application"] == application
    apply.assert_called_once_with(
        dataset, bids_version=None, suffix_aliases=None, severity_threshold="warning"
    )
    planner.assert_not_called()


def test_warning_only_unresolved_repair_does_not_fail_apply(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    application = {
        "applied": [],
        "operations": [],
        "unresolved": [{"code": "UNKNOWN", "severity": "warning"}],
        "journal": None,
        "backups": [],
        "severity_gate": {"threshold": "warning", "passed": True, "exceeding": []},
    }
    with (
        patch(
            "npdb.external.bids.validate_and_repair_bids_dataset",
            return_value={"repair": application},
        ),
    ):
        result = runner.invoke(npdb, ["repair", "bids", str(dataset), "--apply"])

    assert result.exit_code == 0, result.output


def test_repair_bids_rejects_conflicting_aliases(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    result = runner.invoke(
        npdb,
        [
            "repair",
            "bids",
            str(dataset),
            "--suffix-alias",
            "FA=T1w",
            "--suffix-alias",
            "FA=bold",
        ],
    )

    assert result.exit_code == 2
    assert "Conflicting suffix aliases" in result.output


def test_repair_recover_json_calls_recovery_api(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    recovery = {"status": "recovered", "journal": str(dataset / "journal.json")}
    with patch("npdb.bids_repair.recover_bids_repair", return_value=recovery) as recover:
        result = runner.invoke(
            npdb, ["repair", "recover", str(dataset), "--format", "json"]
        )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == recovery
    recover.assert_called_once_with(dataset)


def test_local_conversion_passes_repair_options_to_facade(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "dataset_description.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "output"
    with patch("npdb.cli.facade.DatasetConversionFacade.run") as run:
        result = runner.invoke(
            npdb,
            [
                "convert",
                "bagel",
                "local",
                str(dataset),
                "https://example.com/repo",
                str(output),
                "--bids-version",
                "1.9.0",
                "--suffix-alias",
                "FA=T1w",
                "--suffix-alias",
                "EMG=EMG",
                "--bids-severity-threshold",
                "info",
            ],
        )

    assert result.exit_code == 0, result.output
    run.assert_awaited_once()
    kwargs = run.await_args.kwargs
    assert kwargs["bids_version"] == "1.9.0"
    assert kwargs["suffix_aliases"] == {"FA": "T1w", "EMG": "EMG"}
    assert kwargs["severity_threshold"] == "info"
    assert kwargs["online_url"] == "https://example.com/repo"


@pytest.mark.parametrize(
    "command, identifier",
    [
        ("git", "https://example.com/dataset.git"),
        ("kaggle", "user/dataset"),
        ("mendeley", "dataset-id"),
        ("midrc", "dataset-id"),
        ("openneuro", "ds000001"),
        ("zenodo", "12345"),
        ("figshare", "12345"),
    ],
)
def test_provider_conversion_passes_repair_options_without_fetching_remote_data(
    tmp_path, command, identifier
):
    dataset = tmp_path / "downloaded"
    dataset.mkdir()
    (dataset / "dataset_description.json").write_text("{}", encoding="utf-8")
    provider = Mock()
    provider.provider_name = command
    provider.access_type = "public"
    provider.fetch.return_value = dataset
    with (
        patch(
            "npdb.cli.cli.ProviderManagerFactory.create", return_value=provider
        ) as factory,
        patch("npdb.cli.cli.local2bagel", wraps=local2bagel) as convert,
        patch("npdb.cli.facade.DatasetConversionFacade.run") as run,
    ):
        result = runner.invoke(
            npdb,
            [
                "convert",
                "bagel",
                command,
                identifier,
                str(tmp_path / "output"),
                "--bids-version",
                "1.9.0",
                "--suffix-alias",
                "FA=T1w",
                "--suffix-alias",
                "RARE=T1w",
                "--bids-severity-threshold",
                "error",
            ],
        )

    assert result.exit_code == 0, result.output
    factory.assert_called_once()
    assert factory.call_args.args[0] == command
    provider.fetch.assert_called_once()
    assert provider.fetch.call_args.args[0] == identifier
    convert.assert_called_once()
    assert convert.call_args.kwargs["input_dir"] == dataset
    assert convert.call_args.kwargs["bids_version"] == "1.9.0"
    assert convert.call_args.kwargs["suffix_alias"] == ["FA=T1w", "RARE=T1w"]
    assert convert.call_args.kwargs["bids_severity_threshold"] == "error"
    run.assert_awaited_once()
    assert run.await_args.kwargs["bids_version"] == "1.9.0"
    assert run.await_args.kwargs["suffix_aliases"] == {"FA": "T1w", "RARE": "T1w"}
    assert run.await_args.kwargs["severity_threshold"] == "error"


def test_every_conversion_command_exposes_consistent_repair_options():
    for command in (
        "local",
        "git",
        "kaggle",
        "mendeley",
        "midrc",
        "openneuro",
        "zenodo",
        "figshare",
    ):
        result = runner.invoke(
            npdb, ["convert", "bagel", command, "--help"], env={"COLUMNS": "160"}
        )
        assert result.exit_code == 0, (command, result.output)
        assert "--bids-version" in result.stdout, command
        assert "--suffix-alias" in result.stdout, command
        assert "--bids-severity-threshold" in result.stdout, command


@pytest.mark.parametrize(
    "final_severity,threshold,exit_code",
    [
        (None, "info", 0),
        ("warning", "warning", 0),
        ("warning", "info", 1),
        ("error", "warning", 1),
        ("error", "error", 0),
    ],
)
def test_repair_apply_gates_only_revalidated_findings(
    tmp_path, final_severity, threshold, exit_code
):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    description = dataset / "dataset_description.json"
    description.write_text(
        '{"Name":"Example","BIDSVersion":"1.9.0","Authors":"One author"}'
    )
    initial = {
        "datasets": [{
            "dataset": str(dataset),
            "errors": 1,
            "warnings": 0,
            "issues": [{
                "code": "INVALID_DATASET_DESCRIPTION",
                "severity": "error",
                "message": "Invalid description",
            }],
        }]
    }
    final = {
        "datasets": [{
            "dataset": str(dataset),
            "errors": int(final_severity == "error"),
            "warnings": int(final_severity == "warning"),
            "issues": (
                [{
                    "code": "UNSUPPORTED",
                    "severity": final_severity,
                    "message": "Unresolved",
                    "path": "unresolved-file",
                }] if final_severity else []
            ),
        }]
    }
    with patch(
        "npdb.external.bids.get_bids_validation_report",
        side_effect=[initial, final],
    ) as validate:
        result = runner.invoke(
            npdb,
            [
                "repair", "bids", str(dataset), "--apply", "--format", "json",
                "--bids-severity-threshold", threshold,
            ],
        )
    assert result.exit_code == exit_code, result.output
    assert validate.call_count == 2
    assert json.loads(description.read_text())["Authors"] == ["One author"]
    output = json.loads(result.stdout)
    gate = output["application"]["severity_gate"]
    assert gate["threshold"] == threshold
    assert gate["passed"] == (exit_code == 0)
    assert output["blocking"] == (exit_code != 0)
    assert bool(output["application"]["unresolved"]) == bool(final_severity)


def test_invalid_severity_threshold_is_rejected_before_repair(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    with patch("npdb.external.bids.validate_and_repair_bids_dataset") as repair:
        result = runner.invoke(
            npdb,
            ["repair", "bids", str(dataset), "--apply",
             "--bids-severity-threshold", "invalid"],
        )
    assert result.exit_code == 2
    repair.assert_not_called()


def test_human_apply_output_stays_bounded_for_large_failure_manifest(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    issues = [
        {
            "code": "UNSUPPORTED",
            "severity": "warning",
            "message": f"Detailed warning {index}",
            "path": f"sub-01/file-{index}",
        }
        for index in range(1000)
    ]
    report = {
        "datasets": [{
            "dataset": str(dataset),
            "errors": 0,
            "warnings": len(issues),
            "issues": issues,
        }]
    }
    with patch("npdb.external.bids.get_bids_validation_report", return_value=report):
        result = runner.invoke(
            npdb,
            ["repair", "bids", str(dataset), "--apply",
             "--bids-severity-threshold", "info"],
        )
    assert result.exit_code == 1, result.output
    assert "info=0, warning=1000, error=0" in result.output
    assert "Final severity check (above info): failed" in result.output
    assert "UNSUPPORTED" not in result.output
    assert "Detailed warning" not in result.output
    assert len(result.output.splitlines()) == 3
    manifests = list(
        (dataset.parent / f".{dataset.name}.npdb-bids-repair").glob(
            "*/validation_manifest.json"
        )
    )
    assert len(manifests) == 1
    saved = json.loads(manifests[0].read_text())
    assert len(saved["repair"]["unresolved"]) == 1000
    assert len(saved["repair"]["severity_gate"]["exceeding"]) == 1000


@pytest.mark.parametrize(
    "severity,threshold,exit_code",
    [
        ("warning", "warning", 0),
        ("warning", "info", 1),
        ("error", "warning", 1),
        ("error", "error", 0),
    ],
)
def test_local_conversion_stops_only_when_final_gate_fails(
    tmp_path, severity, threshold, exit_code
):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "dataset_description.json").write_text(
        '{"Name":"Example","BIDSVersion":"1.9.0"}'
    )
    (dataset / "participants.tsv").write_text("participant_id\nsub-01\n")
    report = {
        "datasets": [{
            "dataset": str(dataset),
            "errors": int(severity == "error"),
            "warnings": int(severity == "warning"),
            "issues": [{
                "code": "UNSUPPORTED",
                "severity": severity,
                "message": "Not repairable",
                "path": "unresolved-file",
            }],
        }]
    }
    with (
        patch("npdb.external.bids.get_bids_validation_report", return_value=report),
        patch("npdb.cli.facade.NeurobagelAnnotator.execute", return_value=True) as annotate,
        patch("npdb.cli.facade.BagelNeuroPolyMTL.convert_bids", return_value=None) as convert,
    ):
        result = runner.invoke(
            npdb,
            [
                "convert", "bagel", "local", str(dataset), "https://example.com/repo",
                str(tmp_path / "output"), "--mode", "manual",
                "--bids-severity-threshold", threshold,
            ],
        )
    assert result.exit_code == exit_code, result.output
    assert "UNSUPPORTED" not in result.output
    assert "BIDS validation manifest:" in result.output
    assert f"{severity}=1" in result.output
    if exit_code:
        annotate.assert_not_awaited()
        convert.assert_not_called()
    else:
        annotate.assert_awaited_once()
        convert.assert_called_once()
        accepted = convert.call_args.kwargs["validation_report"]
        assert accepted["datasets"][0]["errors"] == report["datasets"][0]["errors"]
        assert accepted["repair"]["severity_gate"]["passed"]
        assert convert.call_args.kwargs["severity_threshold"] == threshold

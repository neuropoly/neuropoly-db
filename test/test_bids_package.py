"""Offline contracts for the provider-independent package and command."""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import nibabel as nib
import numpy as np
import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner
from typer.testing import CliRunner as NpdbRunner

from bids_repair import (
    get_bids_validation_report,
    plan_bids_repair,
    standardize_dataset,
    validate_and_repair_bids_dataset,
)
from bids_repair.cli import app
from bids_repair.native import bundled_schema_path, schema_document
from bids_repair.tables import (
    apply_header_map,
    fix_single_column_tsv,
    normalize_participants_tsv,
)
from bids_repair.target import target_schema
from npdb.cli.cli import npdb

VERSION = schema_document()["bids_version"]
runner = CliRunner()


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "dataset_description.json").write_text(
        json.dumps(
            {
                "Name": "Local fixture",
                "BIDSVersion": VERSION,
            }
        )
    )
    (root / "participants.tsv").write_text(
        "participant_id\tcustom_measure\nsub-01\t3\n"
    )
    (root / "participants.json").write_text(
        json.dumps({"custom_measure": {"Description": "Fixture measurement"}})
    )
    image = root / "sub-01" / "anat" / "sub-01_T1w.nii.gz"
    image.parent.mkdir(parents=True)
    nib.save(nib.Nifti1Image(np.zeros((4, 4, 4)), np.eye(4)), image)
    return root


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def test_default_plan_and_validate_are_read_only_and_versioned(dataset):
    before = snapshot(dataset)
    for arguments in (
        [str(dataset), "--dry-run"],
        ["validate", str(dataset)],
    ):
        result = runner.invoke(
            app, [*arguments, "--bids-version", VERSION, "--format", "json"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["target_bids_version"] == VERSION
        assert "beta" in payload["coverage"]
        assert "metadata" in payload["coverage"]
        assert "NIfTI" in payload["coverage"]
        assert snapshot(dataset) == before


def test_schema_findings_use_requested_schema_not_declared_version(dataset):
    description = dataset / "dataset_description.json"
    description.write_text('{"Name":"Example","BIDSVersion":"1.0.0"}')
    invalid = dataset / "sub-01" / "anat" / "sub-01_notABidsSuffix.nii.gz"
    invalid.touch()
    before = description.read_bytes()

    report = get_bids_validation_report(dataset, target_version=VERSION)

    assert report["target_bids_version"] == VERSION
    assert any(
        issue.get("path") == str(invalid) and issue["severity"] == "error"
        for item in report["datasets"]
        for issue in item["issues"]
    )
    assert description.read_bytes() == before


def test_real_schema_findings_do_not_flood_human_apply_output(dataset):
    for index in range(60):
        (dataset / "sub-01" / "anat" / f"sub-01_unsupported{index}.nii.gz").touch()

    result = runner.invoke(
        app,
        [
            str(dataset),
            "--apply",
            "--bids-version",
            VERSION,
            "--bids-severity-threshold",
            "info",
        ],
    )

    assert result.exit_code == 1, result.output
    assert len(result.output.splitlines()) == 3, result.output
    assert "Final severity check (above info): failed" in result.output
    manifests = list(
        (dataset.parent / f".{dataset.name}.npdb-bids-repair").glob(
            "*/validation_manifest.json"
        )
    )
    report = json.loads(manifests[0].read_text())
    assert len(report["repair"]["unresolved"]) >= 60


def test_unsupported_version_fails_without_changing_dataset(dataset):
    before = snapshot(dataset)

    result = runner.invoke(app, [str(dataset), "--apply", "--bids-version", "0.0.0"])

    assert result.exit_code == 1
    assert "Unsupported target" in " ".join(result.output.split())
    assert "cannot retarget" in " ".join(result.output.split())
    assert snapshot(dataset) == before


def test_local_matching_schema_is_used_without_fallback(dataset, tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text(bundled_schema_path().read_text())

    with target_schema(VERSION, schema) as selected:
        assert selected == schema
    with pytest.raises(ValueError, match="Unsupported target"):
        with target_schema("0.0.0", schema):
            pytest.fail("Mismatched schema must not be selected")
    report = get_bids_validation_report(
        dataset, target_version=VERSION, schema_path=schema
    )

    assert report["target_bids_version"] == VERSION


def test_missing_version_is_an_actionable_unattended_usage_error(dataset):
    (dataset / "dataset_description.json").unlink()

    with patch("bids_repair.cli._interactive", return_value=False):
        result = runner.invoke(app, [str(dataset)])

    assert result.exit_code == 2
    assert "Supply --bids-version" in result.output


def test_interactive_command_asks_for_version(dataset):
    (dataset / "dataset_description.json").unlink()

    with patch("bids_repair.cli._interactive", return_value=True):
        result = runner.invoke(app, [str(dataset)], input=f"{VERSION}\n")

    assert result.exit_code == 0, result.output
    assert "Target BIDS standard version" in result.output


@pytest.mark.parametrize("name", ["validate", "recover", "standardize"])
def test_dataset_names_matching_subcommands_are_addressable(
    tmp_path, name, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / name
    root.mkdir()
    for path in (f"./{name}", str(root)):
        with patch(
            "bids_repair.cli.plan_bids_repair", return_value={"findings": []}
        ) as plan:
            result = runner.invoke(app, [path, "--bids-version", VERSION])
        assert result.exit_code == 0, result.output
        assert plan.call_args.args == (root,)
    with patch("bids_repair.cli.plan_bids_repair", return_value={"findings": []}):
        result = runner.invoke(app, ["--", name, "--bids-version", VERSION])
    assert result.exit_code == 0, result.output


def test_recovery_does_not_ask_for_version(dataset):
    with (
        patch("bids_repair.cli._target_version") as version,
        patch(
            "bids_repair.cli.recover_bids_repair", return_value={"message": "Recovered"}
        ),
    ):
        result = runner.invoke(app, ["recover", str(dataset)])
    assert result.exit_code == 0
    version.assert_not_called()


def test_standardization_has_no_ontology_requirement_and_dry_run_is_exact(dataset):
    participants = dataset / "participants.tsv"
    participants.write_text('SubjectID;CustomMeasure\n"sub-01";"3"\n"sub-01";"3"\n')
    before = snapshot(dataset)
    options = {
        "header_map": {
            "participant_id": {"aliases": ["SubjectID"]},
            "custom_measure": {"aliases": ["CustomMeasure"]},
        },
        "column_metadata": {
            "custom_measure": {"Description": "Custom measurement", "Units": "mm"}
        },
        "target_version": VERSION,
    }
    preview = standardize_dataset(dataset, **options, dry_run=True)
    assert snapshot(dataset) == before
    result = standardize_dataset(dataset, **options)
    assert result["participants_json"] == preview["participants_json"]
    assert result["participants_json"]["custom_measure"]["Units"] == "mm"
    assert "nb:" not in json.dumps(result)
    assert "age" not in result["participants_json"]


def test_explicit_layout_preparation_is_previewed_without_source_writes(tmp_path):
    root = tmp_path / "dataset"
    raw = root / "rawdata"
    raw.mkdir(parents=True)
    (raw / "sub-01_T1w.nii.gz").touch()
    before = snapshot(root)
    result = standardize_dataset(root, prepare_layout=True, dry_run=True)
    assert result["prepared_layout"]
    assert snapshot(root) == before
    standardize_dataset(root, prepare_layout=True)
    assert not raw.exists()
    assert (root / "participants.tsv").exists()


def test_standardization_preview_does_not_copy_image_payloads(dataset):
    import shutil

    with patch("bids_repair.standardize.shutil.copy2", wraps=shutil.copy2) as copy:
        standardize_dataset(dataset, dry_run=True)
    assert copy.call_count > 0
    assert all(
        Path(call.args[0]).name in {"participants.tsv", "participants.json"}
        for call in copy.call_args_list
    )


def test_invalid_metadata_is_not_silently_accepted(dataset):
    before = snapshot(dataset)
    result = runner.invoke(
        app,
        [
            "standardize",
            str(dataset),
            "--bids-version",
            VERSION,
            "--column-metadata",
            str(dataset / "dataset_description.json"),
        ],
    )
    assert result.exit_code == 1
    assert "Column metadata must map" in result.output
    assert snapshot(dataset) == before


def test_column_collision_and_quoted_delimiter_are_preserved(tmp_path):
    table = tmp_path / "participants.tsv"
    table.write_text('participant_id,comment\nsub-01,"text, with comma"\n')
    fix_single_column_tsv(table)
    assert '"text, with comma"' not in table.read_text()
    assert "text, with comma" in table.read_text()
    table.write_text("participant_id\tSubjectID\nsub-01\tsub-02\n")
    with pytest.raises(ValueError, match="Conflicting"):
        apply_header_map(table, {"participant_id": {"aliases": ["SubjectID"]}})


def test_cleanup_preserves_quoted_multiline_cells_and_tolerates_blank_lines(tmp_path):
    table = tmp_path / "participants.tsv"
    table.write_text('participant_id,comment\nsub-01,"first,\nsecond"\n\n')
    normalize_participants_tsv(table)
    import csv

    with table.open(newline="") as stream:
        rows = [row for row in csv.reader(stream, delimiter="\t") if row]
    assert rows == [["participant_id", "comment"], ["sub-01", "first,\nsecond"]]


def test_npdb_keeps_optional_version_and_never_prompts(dataset):
    with (
        patch(
            "bids_repair.validation.get_bids_validation_report",
            return_value={
                "datasets": [
                    {"dataset": str(dataset), "errors": 0, "warnings": 0, "issues": []}
                ],
            },
        ),
        patch("builtins.input", side_effect=AssertionError("Library must not prompt")),
    ):
        report = validate_and_repair_bids_dataset(dataset)
    assert report["repair"]["resolved_bids_version"] is None


def test_old_npdb_bids_commands_are_removed():
    for command in ("repair", "standardize"):
        result = NpdbRunner().invoke(npdb, [command, "--help"])
        assert result.exit_code == 2
        assert "No such command" in result.output


def test_provider_commands_and_automation_are_absent_from_bids_cli():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    command = get_command(app)
    from typer.core import TyperGroup

    assert isinstance(command, TyperGroup)
    assert "convert" not in command.commands
    assert "midrc" not in command.commands
    result = runner.invoke(app, ["standardize", "--help"])
    assert result.exit_code == 0
    for option in ("--ai-provider", "--ai-model", "--headed", "--phenotype-dict"):
        assert option not in result.output


def test_cli_uses_typer_rich_help_and_option_panels(dataset):
    assert isinstance(app, typer.Typer)
    for arguments in (["--help"], [str(dataset), "--help"], ["standardize", "--help"]):
        result = runner.invoke(app, arguments, color=True)
        assert result.exit_code == 0, result.output
        assert "╭" in result.output
    for panel in ("Input Options", "Output Options", "Behavior Options"):
        assert panel in result.output
    assert runner.invoke(app, ["-h"]).exit_code == 0


def test_rich_human_output_preserves_literal_paths_and_findings(dataset):
    message = "[red]literal metadata[/red]"
    with patch(
        "bids_repair.cli.plan_bids_repair",
        return_value={
            "dataset": str(dataset / "[bold]local"),
            "findings": [{"severity": "warning", "message": message}],
        },
    ):
        result = runner.invoke(app, [str(dataset), "--bids-version", VERSION])
    assert result.exit_code == 0, result.output
    assert "[bold]local" in result.output
    assert message in result.output


def test_import_boundary_including_lazy_imports():
    package = Path(__file__).resolve().parents[1] / "src" / "bids_repair"
    forbidden = {"npdb", "bagel", "gitea", "gen3", "playwright"}
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden, path
            elif isinstance(node, ast.Import):
                assert all(
                    alias.name.split(".")[0] not in forbidden for alias in node.names
                ), path
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import pkgutil, importlib, sys, bids_repair; "
                "[importlib.import_module(m.name) for m in pkgutil.walk_packages("
                "bids_repair.__path__, bids_repair.__name__ + '.')]; "
                "assert not any(n.split('.')[0] in {'npdb','bagel','gitea','gen3','playwright'} "
                "for n in sys.modules)"
            ),
        ],
        env={**os.environ, "PYTHONPATH": str(package.parent)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

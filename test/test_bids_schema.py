"""Exact native-schema compatibility and standalone CLI target selection."""

import json
import socket
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from bids_repair.cli import app
from bids_repair.native import bundled_schema_path, schema_document
from bids_repair.target import acquire_target_schema, target_schema

VERSION = schema_document()["bids_version"]
runner = CliRunner()


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "dataset_description.json").write_text(
        json.dumps({"Name": "Fixture", "BIDSVersion": VERSION})
    )
    return root


def test_matching_target_and_identical_local_schema_are_offline(tmp_path):
    local = tmp_path / "schema.json"
    local.write_text(json.dumps(schema_document(), indent=2))
    with patch.object(
        socket, "create_connection", side_effect=AssertionError("network")
    ):
        assert acquire_target_schema(VERSION) is None
        assert acquire_target_schema(VERSION, local) == local
        with target_schema(VERSION, local) as selected:
            assert selected == local


def test_identical_schema_directory_is_compiled_and_accepted(tmp_path):
    document = schema_document()
    (tmp_path / "BIDS_VERSION").write_text(document["bids_version"])
    (tmp_path / "SCHEMA_VERSION").write_text(document["schema_version"])
    for key, value in document.items():
        if key not in {"bids_version", "schema_version"}:
            (tmp_path / f"{key}.yaml").write_text(json.dumps(value))

    with patch.object(
        socket, "create_connection", side_effect=AssertionError("network")
    ):
        assert acquire_target_schema(VERSION, tmp_path) == tmp_path


@pytest.mark.parametrize("version", ["1.9.0", "0.0.0", "../1.9.0", "latest", ""])
def test_incompatible_target_rejected_without_network(version):
    with (
        patch.object(
            socket, "create_connection", side_effect=AssertionError("network")
        ),
        pytest.raises(ValueError, match="Unsupported target BIDS version"),
    ):
        acquire_target_schema(version)


@pytest.mark.parametrize("content", ["{}", "[]", "broken", '{"bids_version":"1.11.1"}'])
def test_custom_schema_must_be_structurally_identical(tmp_path, content):
    path = tmp_path / "schema.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        acquire_target_schema(VERSION, path)


def test_same_version_modified_rules_are_not_accepted(tmp_path):
    document = schema_document()
    document["rules"] = {}
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="structurally identical"):
        acquire_target_schema(VERSION, path)


@pytest.mark.parametrize(
    "command,function",
    [
        ([], "plan_bids_repair"),
        (["validate"], "get_bids_validation_report"),
        (["standardize"], "standardize_dataset"),
        (["plan"], "plan_bids_repair"),
    ],
)
def test_dataset_version_is_default_without_prompt(dataset, command, function):
    with (
        patch(
            f"bids_repair.cli.{function}", return_value={"datasets": [], "findings": []}
        ) as operation,
        patch("bids_repair.cli._interactive", return_value=False),
        patch("bids_repair.cli.typer.prompt") as prompt,
    ):
        result = runner.invoke(app, [*command, str(dataset), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)
    assert operation.call_args.kwargs["target_version"] == VERSION
    assert operation.call_args.kwargs["schema_path"] is None
    prompt.assert_not_called()


@pytest.mark.parametrize("command", [[], ["--apply"], ["validate"], ["standardize"]])
def test_unsupported_target_fails_before_any_operation(dataset, command):
    before = (dataset / "dataset_description.json").read_bytes()
    args = (
        [command[0], str(dataset)]
        if command and not command[0].startswith("--")
        else [str(dataset), *command]
    )
    with (
        patch("bids_repair.cli.plan_bids_repair") as plan,
        patch("bids_repair.cli.validate_and_repair_bids_dataset") as apply,
        patch("bids_repair.cli.get_bids_validation_report") as validate,
        patch("bids_repair.cli.standardize_dataset") as standardize,
    ):
        result = runner.invoke(
            app, [*args, "--bids-version", "1.9.0", "--format", "json"]
        )
    assert result.exit_code == 1
    assert "supports BIDS" in json.loads(result.stdout)["error"]["message"]
    for operation in (plan, apply, validate, standardize):
        operation.assert_not_called()
    assert (dataset / "dataset_description.json").read_bytes() == before


def test_latest_resolving_to_unsupported_target_fails(dataset):
    with (
        patch("bids_repair.cli.resolve_bids_version", return_value="999.0.0"),
        patch("bids_repair.cli.plan_bids_repair") as operation,
    ):
        result = runner.invoke(app, [str(dataset), "--bids-version", "latest"])
    assert result.exit_code == 1
    assert "Unsupported target" in result.output
    operation.assert_not_called()


@pytest.mark.parametrize("content", ["{}", "[]", "broken", '{"BIDSVersion":null}'])
def test_invalid_declaration_requires_unattended_target(dataset, content):
    (dataset / "dataset_description.json").write_text(content)
    with patch("bids_repair.cli._interactive", return_value=False):
        result = runner.invoke(app, [str(dataset)])
    assert result.exit_code == 2
    assert "Supply --bids-version" in result.output


def test_explicit_target_warns_without_changing_metadata(dataset):
    description = dataset / "dataset_description.json"
    description.write_text('{"Name":"Fixture","BIDSVersion":"1.0.0"}')
    before = description.read_bytes()
    with patch("bids_repair.cli.plan_bids_repair", return_value={"findings": []}):
        result = runner.invoke(
            app, [str(dataset), "--bids-version", VERSION, "--format", "json"]
        )
    assert result.exit_code == 0, result.output
    assert result.stderr.count("differs") == 1
    assert description.read_bytes() == before


def test_interactive_prompt_keeps_json_on_stdout(dataset):
    (dataset / "dataset_description.json").unlink()
    with (
        patch("bids_repair.cli._interactive", return_value=True),
        patch("bids_repair.cli.plan_bids_repair", return_value={"findings": []}),
    ):
        result = runner.invoke(
            app, [str(dataset), "--format", "json"], input=f"{VERSION}\n"
        )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"findings": []}
    assert "Target BIDS standard version" in result.stderr

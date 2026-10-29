import json
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from npdb.bids_repair import inspect_dataset_description, plan_bids_repair
from npdb.cli.cli import npdb
from npdb.external.bids import (
    get_bids_validation_report,
    validate_and_repair_bids_dataset,
)
from npdb.external.bids_version import LATEST_RELEASE_URL, resolve_bids_version


def _release_response(payload, status=200):
    return httpx.Response(
        status, json=payload, request=httpx.Request("GET", LATEST_RELEASE_URL)
    )


def _dataset(tmp_path, description):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "dataset_description.json").write_text(json.dumps(description))
    (root / "participants.tsv").write_text("participant_id\nsub-01\n")
    (root / "sub-01").mkdir()
    return root


def test_latest_version_uses_official_stable_release():
    with patch(
        "npdb.external.bids_version.httpx.get",
        return_value=_release_response(
            {"tag_name": "v1.11.2", "draft": False, "prerelease": False}
        ),
    ) as get:
        assert resolve_bids_version("latest") == "1.11.2"
    assert get.call_args.args == (LATEST_RELEASE_URL,)
    assert get.call_args.kwargs["timeout"] == 30
    assert get.call_args.kwargs["follow_redirects"] is False


@pytest.mark.parametrize("version", [None, "1.9.0", " 1.10.1 "])
def test_explicit_versions_do_not_require_network(version):
    with patch("npdb.external.bids_version.httpx.get") as get:
        assert resolve_bids_version(version) == (
            version.strip() if isinstance(version, str) else None
        )
    get.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"tag_name": "v1.12.0rc1", "draft": False, "prerelease": True},
        {"tag_name": "v1.12.0", "draft": True, "prerelease": False},
        {"tag_name": "latest", "draft": False, "prerelease": False},
    ],
)
def test_latest_rejects_unusable_release_metadata(payload):
    with (
        patch(
            "npdb.external.bids_version.httpx.get",
            return_value=_release_response(payload),
        ),
        pytest.raises(RuntimeError),
    ):
        resolve_bids_version("latest")


@pytest.mark.parametrize("failure", ["http", "timeout", "json"])
def test_latest_failures_are_explicit_and_do_not_modify_dataset(tmp_path, failure):
    root = _dataset(tmp_path, {"Name": "Example"})
    original = (root / "dataset_description.json").read_bytes()
    response = _release_response({}, status=403 if failure == "http" else 200)
    if failure == "json":
        response = httpx.Response(
            200, content=b"invalid", request=httpx.Request("GET", LATEST_RELEASE_URL)
        )
    with (
        patch(
            "npdb.external.bids_version.httpx.get",
            return_value=response,
            side_effect=httpx.TimeoutException("offline") if failure == "timeout" else None,
        ),
        patch("npdb.external.bids.get_bids_validation_report") as validate,
        pytest.raises(RuntimeError),
    ):
        validate_and_repair_bids_dataset(root, bids_version="latest")
    validate.assert_not_called()
    assert (root / "dataset_description.json").read_bytes() == original


def test_latest_real_validator_apply_records_concrete_version(tmp_path):
    root = _dataset(
        tmp_path,
        {
            "Name": "Spinal dataset fixture",
            "Authors": "One author, another author",
            "ReferencesAndLinks": ["Publication"],
        },
    )
    with patch(
        "npdb.external.bids_version.httpx.get",
        return_value=_release_response(
            {"tag_name": "v1.11.2", "draft": False, "prerelease": False}
        ),
    ) as get:
        result = CliRunner().invoke(
            npdb, ["repair", "bids", str(root), "--apply", "--bids-version", "latest"]
        )
    assert result.exit_code == 0, result.output
    assert get.call_count == 1
    saved = json.loads((root / "dataset_description.json").read_text())
    assert saved["BIDSVersion"] == "1.11.2"
    assert saved["Authors"] == ["One author, another author"]
    manifests = list(
        (tmp_path / ".dataset.npdb-bids-repair").glob("*/validation_manifest.json")
    )
    report = json.loads(manifests[0].read_text())
    assert report["repair"]["requested_bids_version"] == "latest"
    assert report["repair"]["resolved_bids_version"] == "1.11.2"
    assert report["repair"]["severity_gate"]["passed"]
    assert not any(
        finding["code"] == "INVALID_DATASET_DESCRIPTION"
        for finding in report["repair"]["unresolved"]
    )
    initial = report["repair"]["original_report"]["datasets"][0]["issues"]
    description_issue = next(
        issue for issue in initial if issue["code"] == "INVALID_DATASET_DESCRIPTION"
    )
    assert description_issue["diagnostics"]["json_valid"]
    assert "BIDSVersion is missing" in description_issue["message"]
    assert "Authors must be an array" in description_issue["message"]


def test_latest_dry_run_preserves_files_and_existing_valid_version(tmp_path):
    root = _dataset(tmp_path, {"Name": "Example", "BIDSVersion": "1.9.0", "Authors": "Author"})
    original = (root / "dataset_description.json").read_bytes()
    with patch(
        "npdb.external.bids_version.httpx.get",
        return_value=_release_response(
            {"tag_name": "v1.11.2", "draft": False, "prerelease": False}
        ),
    ):
        plan = plan_bids_repair(root, bids_version="latest")
    assert plan["bids_version"] == "1.11.2"
    assert json.loads(plan["operations"][0]["content"])["BIDSVersion"] == "1.9.0"
    assert (root / "dataset_description.json").read_bytes() == original


def test_latest_local_conversion_records_version_before_downstream_conversion(tmp_path):
    root = _dataset(tmp_path, {"Name": "Example", "Authors": "Author"})
    with (
        patch(
            "npdb.external.bids_version.httpx.get",
            return_value=_release_response(
                {"tag_name": "v1.11.2", "draft": False, "prerelease": False}
            ),
        ) as get,
        patch("npdb.cli.facade.NeurobagelAnnotator.execute", return_value=True),
        patch("npdb.cli.facade.BagelNeuroPolyMTL.convert_bids", return_value=None) as convert,
    ):
        result = CliRunner().invoke(
            npdb,
            [
                "convert", "bagel", "local", str(root), "https://example.com/dataset",
                str(tmp_path / "output"), "--mode", "manual", "--bids-version", "latest",
            ],
        )
    assert result.exit_code == 0, result.output
    get.assert_called_once()
    convert.assert_called_once()
    description = convert.call_args.kwargs["dataset_description"]
    assert description["BIDSVersion"] == "1.11.2"
    assert description["RepositoryURL"] == "https://example.com/dataset"
    accepted = convert.call_args.kwargs["validation_report"]
    assert accepted["repair"]["resolved_bids_version"] == "1.11.2"
    assert accepted["repair"]["severity_gate"]["passed"]


def test_empty_version_is_repaired_with_explicit_latest(tmp_path):
    root = _dataset(tmp_path, {"Name": "Example", "BIDSVersion": ""})
    report = {
        "datasets": [{
            "dataset": str(root), "errors": 1, "warnings": 0,
            "issues": [{
                "code": "INVALID_DATASET_DESCRIPTION",
                "severity": "error",
                "message": "Invalid description",
            }],
        }]
    }
    with (
        patch(
            "npdb.external.bids_version.httpx.get",
            return_value=_release_response(
                {"tag_name": "v1.11.2", "draft": False, "prerelease": False}
            ),
        ),
        patch("npdb.external.bids.get_bids_validation_report", return_value=report),
    ):
        plan = plan_bids_repair(root, bids_version="latest")
    assert json.loads(plan["operations"][0]["content"])["BIDSVersion"] == "1.11.2"
    assert not plan["blocking"]


@pytest.mark.parametrize(
    "description,problem",
    [
        ({}, "Name is missing"),
        ({"Name": 3, "BIDSVersion": "1.9.0"}, "Name must be a non-empty string"),
        ({"Name": "Example"}, "BIDSVersion is missing"),
        ({"Name": "Example", "BIDSVersion": "1.9.0", "Authors": 3}, "Authors must be an array"),
        ({"Name": "Example", "BIDSVersion": "1.9.0", "GeneratedBy": [{}]}, "GeneratedBy[0].Name"),
    ],
)
def test_valid_json_description_errors_explain_fields(tmp_path, description, problem):
    root = _dataset(tmp_path, description)
    report = get_bids_validation_report(root)
    issue = next(
        item for item in report["datasets"][0]["issues"]
        if item["code"] == "INVALID_DATASET_DESCRIPTION"
    )
    assert issue["diagnostics"]["json_valid"]
    assert problem in issue["message"]


def test_malformed_json_includes_parse_location(tmp_path):
    root = _dataset(tmp_path, {})
    (root / "dataset_description.json").write_text('{\n"Name":\n}')
    report = get_bids_validation_report(root)
    issue = next(
        item for item in report["datasets"][0]["issues"]
        if item["code"] == "INVALID_DATASET_DESCRIPTION"
    )
    assert issue["diagnostics"]["json_valid"] is False
    assert "line 3, column 1" in issue["message"]


def test_valid_basic_fields_do_not_silently_override_validator_error(tmp_path):
    root = _dataset(tmp_path, {"Name": "Example", "BIDSVersion": "1.9.0"})
    diagnostics = inspect_dataset_description(root)
    assert diagnostics["json_valid"]
    assert diagnostics["problems"] == []
    report = {
        "datasets": [{
            "dataset": str(root),
            "errors": 1,
            "issues": [{
                "code": "INVALID_DATASET_DESCRIPTION",
                "message": "dataset_description.json cannot be parsed",
                "severity": "error",
            }],
        }]
    }
    from subprocess import CompletedProcess

    with patch(
        "npdb.external.bids.subprocess.run",
        return_value=CompletedProcess([], 1, json.dumps(report), ""),
    ):
        inspected = get_bids_validation_report(root)
    assert inspected["datasets"][0]["errors"] == 1
    assert "basic field checks passed" in inspected["datasets"][0]["issues"][0]["message"]

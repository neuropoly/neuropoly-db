import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from npdb.external.bids import (
    _adapt_report,
    get_bids_validation_report,
    validate_bids_dataset,
)


def test_uses_only_the_rust_validator(tmp_path):
    upstream = {
        "issues": {
            "issues": [{"code": "WARN", "severity": "warning"}],
            "codeMessages": {"WARN": "A warning"},
        }
    }
    with (
        patch(
            "npdb.external.bids.validator_executable",
            return_value="/tools/bids-validator",
        ) as which,
        patch(
            "npdb.external.bids.subprocess.run",
            return_value=Mock(returncode=0, stdout=json.dumps(upstream)),
        ) as run,
    ):
        report = validate_bids_dataset(tmp_path)
        assert report["datasets"][0]["warnings"] == 1
        assert report["upstream_report"] == upstream
    which.assert_called_once_with()
    run.assert_called_once_with(
        [
            "/tools/bids-validator",
            str(tmp_path.resolve()),
            "--format",
            "json",
            "--recursive",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_missing_rust_validator_does_not_fall_back(tmp_path):
    with (
        patch("npdb.external.native.shutil.which", return_value=None),
        patch("npdb.external.native.sys.executable", str(tmp_path / "python")),
        patch("npdb.external.bids.subprocess.run") as run,
        pytest.raises(RuntimeError, match="install_bids_validator.sh"),
    ):
        validate_bids_dataset(tmp_path)
    run.assert_not_called()


def test_workspace_image_includes_only_native_validation_tools():
    root = Path(__file__).resolve().parents[1]
    dockerfile = " ".join(
        (root / ".devcontainer/Dockerfile").read_text().replace("\\\n", " ").split()
    )
    devcontainer = json.loads((root / ".devcontainer/devcontainer.json").read_text())
    rust_feature = devcontainer["features"]["ghcr.io/devcontainers/features/rust:1"]
    assert "FROM rust:1.90.0-bookworm AS validator" in dockerfile
    assert "build_validator.py --output /native/npdb-validator" in dockerfile
    assert "apt-get install -y --no-install-recommends dcm2niix" in dockerfile
    assert all(package in dockerfile for package in ("clang", "lld", "llvm"))
    assert rust_feature["version"] == "1.90.0"
    assert rust_feature["targets"] == "x86_64-pc-windows-msvc"
    assert "COPY --from=validator /native/npdb-validator" in dockerfile
    post_create = (root / ".devcontainer/postCreate.sh").read_text()
    assert "cargo install --locked cargo-xwin" in post_create
    assert "npm" not in dockerfile
    assert "deno" not in dockerfile
    ignored = (root / ".dockerignore").read_text().splitlines()
    assert ignored[0] == "**"
    assert "!tools/bids-validator-rust/Cargo.lock" in ignored
    assert "!tools/bids-validator-rust/crates/**" in ignored
    assert "!tools/bids-validator-rust/schemas/**" in ignored
    for path in (
        "tools/validator_artifacts.py", "tools/audit_validator.py",
        "src/npdb/external/validator-artifacts.json",
        "src/npdb/external/validator-artifact-notices.txt.gz",
    ):
        assert f"!{path}" in ignored
        assert path in dockerfile
    assert ignored[-1] == "**/.git"
    assert not (root / ".devcontainer/Dockerfile.dockerignore").exists()
    assert not any(line.startswith("!tmp") for line in ignored)


def test_neurobagel_validation_gate_uses_rust_instead_of_pybids(tmp_path):
    import bagel.cli as bagel_cli

    from npdb.managers.neurobagel import BagelMixin

    report = {"datasets": [{"errors": 0}]}
    manager = BagelMixin(Mock(root=str(tmp_path)))

    def invoke(arguments):
        assert arguments[0] == "bids2tsv"
        assert bagel_cli.BIDSLayout(tmp_path, validate=True) == report
        return Mock(exit_code=0)

    manager.cli = Mock()
    manager.cli.invoke.side_effect = lambda app, arguments: invoke(arguments)
    with (
        patch(
            "npdb.managers.neurobagel.validate_and_repair_bids_dataset",
            return_value={**report, "repair": {"final_report": report}},
        ) as validation,
        patch("bagel.cli.BIDSLayout") as original_layout,
    ):
        manager.bids2tsv(str(tmp_path), str(tmp_path / "bids.tsv"))
        assert bagel_cli.BIDSLayout is original_layout
    validation.assert_called_once_with(str(tmp_path), severity_threshold="warning")
    original_layout.assert_not_called()


@pytest.mark.parametrize(
    "code, stdout",
    [
        (1, "validation failed"),
        (0, "not json"),
        (0, '{"datasets": [{"errors": 1}]}'),
        (2, '{"issues": {"issues": [], "codeMessages": {}}}'),
        (1, '{"issues": {"issues": [], "codeMessages": {}}}'),
        (
            0,
            '{"issues": {"issues": [{"code": "ERR", "severity": "error"}], "codeMessages": {}}}',
        ),
    ],
)
def test_rejects_failed_or_invalid_validator_results(tmp_path, code, stdout):
    with (
        patch(
            "npdb.external.bids.validator_executable",
            return_value="/tools/bids-validator",
        ),
        patch(
            "npdb.external.bids.subprocess.run",
            return_value=Mock(returncode=code, stdout=stdout, stderr="error"),
        ),
        pytest.raises(RuntimeError),
    ):
        validate_bids_dataset(tmp_path)


def test_recursive_reports_preserve_upstream_evidence_and_derivative_ownership(
    tmp_path,
):
    child = tmp_path / "derivatives" / "pipeline"
    child.mkdir(parents=True)
    nested = child / "derivatives" / "nested"
    nested.mkdir(parents=True)
    raw = {"issues": {"issues": [], "codeMessages": {}}}
    upstream = {
        **raw,
        "derivativesSummary": {
            "pipeline": {
                "issues": {
                    "issues": [
                        {
                            "code": "NIFTI_HEADER_UNREADABLE",
                            "severity": "error",
                            "location": "/sub-01/anat/sub-01_T1w.nii.gz",
                            "issueMessage": "Bad header",
                            "subCode": "DETAIL",
                        }
                    ],
                    "codeMessages": {"NIFTI_HEADER_UNREADABLE": "Cannot parse"},
                },
                "derivativesSummary": {"nested": raw},
            }
        },
    }
    with (
        patch(
            "npdb.external.bids.validator_executable",
            return_value="/tools/bids-validator",
        ),
        patch(
            "npdb.external.bids.subprocess.run",
            return_value=Mock(returncode=1, stdout=json.dumps(upstream)),
        ),
    ):
        report = validate_bids_dataset(tmp_path)
    assert len(report["datasets"]) == 3
    assert report["datasets"][1]["dataset"] == str(child)
    assert report["datasets"][2]["dataset"] == str(nested)
    issue = report["datasets"][1]["issues"][0]
    assert issue["code"] == "NIFTI_HEADER_UNREADABLE"
    assert issue["subCode"] == "DETAIL"
    assert issue["severity"] == "error"
    assert issue["message"] == "Cannot parse: Bad header"
    assert issue["path"] == str(child / "sub-01/anat/sub-01_T1w.nii.gz")
    assert report["upstream_report"] == upstream


@pytest.mark.parametrize(
    "issue",
    [
        {"code": "ERR", "severity": "unknown"},
        {"code": "ERR", "severity": []},
        {"code": "ERR", "severity": "error", "location": "/../outside"},
        {"code": "", "severity": "error"},
    ],
)
def test_malformed_findings_are_not_success(tmp_path, issue):
    with pytest.raises(RuntimeError):
        _adapt_report(tmp_path, {"issues": {"issues": [issue], "codeMessages": {}}})


def test_unsafe_recursive_dataset_is_rejected(tmp_path):
    with pytest.raises(RuntimeError, match="Unsafe derivative"):
        _adapt_report(
            tmp_path,
            {
                "issues": {"issues": [], "codeMessages": {}},
                "derivativesSummary": {"../outside": {}},
            },
        )


def test_validator_execution_failure_is_actionable(tmp_path):
    with (
        patch("npdb.external.bids.validator_executable", return_value="/bad/binary"),
        patch(
            "npdb.external.bids.subprocess.run", side_effect=OSError("bad architecture")
        ),
        pytest.raises(RuntimeError, match="Cannot execute.*bad architecture"),
    ):
        get_bids_validation_report(tmp_path)

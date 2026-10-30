import hashlib
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from bids_repair import native
from bids_repair.validation import get_bids_validation_report, validate_bids_dataset


def upstream_report(*issues):
    return {
        "issues": {
            "issues": list(issues),
            "codeMessages": {issue["code"]: "Explanation" for issue in issues},
        }
    }


def test_uses_pinned_rust_json_interface(tmp_path):
    document = upstream_report(
        {
            "code": "NO_AUTHORS",
            "severity": "warning",
            "location": "/dataset_description.json",
        }
    )
    with (
        patch(
            "bids_repair.validation._validator_executable",
            return_value="/tools/bids-validator",
        ),
        patch(
            "bids_repair.validation.subprocess.run",
            return_value=Mock(returncode=0, stdout=json.dumps(document), stderr=""),
        ) as run,
    ):
        report = validate_bids_dataset(tmp_path)
    assert report["datasets"][0]["warnings"] == 1
    assert report["datasets"][0]["issues"][0]["path"] == str(
        tmp_path / "dataset_description.json"
    )
    run.assert_called_once_with(
        [
            "/tools/bids-validator",
            str(tmp_path),
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


def test_keeps_immediate_derivative_boundary_and_missing_metadata(tmp_path):
    child = tmp_path / "derivatives" / "pipeline"
    child.mkdir(parents=True)
    (child / "derivatives" / "nested").mkdir(parents=True)
    with (
        patch("bids_repair.validation._validator_executable", return_value="validator"),
        patch(
            "bids_repair.validation.subprocess.run",
            return_value=Mock(
                returncode=0, stdout=json.dumps(upstream_report()), stderr=""
            ),
        ) as run,
    ):
        report = get_bids_validation_report(tmp_path)
    assert [item["dataset"] for item in report["datasets"]] == [
        str(tmp_path),
        str(child),
    ]
    assert any(
        issue["code"] == "DERIVATIVE_PROVENANCE"
        for issue in report["datasets"][1]["issues"]
    )
    assert run.call_count == 2


@pytest.mark.parametrize(
    "code, document",
    [
        (1, upstream_report()),
        (2, upstream_report()),
        (0, {"datasets": []}),
        (0, {"issues": {"issues": [], "codeMessages": []}}),
        (0, upstream_report({"code": "BAD", "severity": "info"})),
        (
            1,
            upstream_report(
                {"code": "BAD", "severity": "error", "location": "/../outside"}
            ),
        ),
        (0, upstream_report({"code": "BAD", "severity": "error"})),
    ],
)
def test_rejects_failed_or_invalid_validator_results(tmp_path, code, document):
    with (
        patch("bids_repair.validation._validator_executable", return_value="validator"),
        patch(
            "bids_repair.validation.subprocess.run",
            return_value=Mock(
                returncode=code, stdout=json.dumps(document), stderr="error"
            ),
        ),
        pytest.raises(RuntimeError),
    ):
        get_bids_validation_report(tmp_path)


def test_invalid_json_is_explicit(tmp_path):
    with (
        patch("bids_repair.validation._validator_executable", return_value="validator"),
        patch(
            "bids_repair.validation.subprocess.run",
            return_value=Mock(returncode=1, stdout="not json", stderr="failure"),
        ),
        pytest.raises(RuntimeError, match="structured report"),
    ):
        get_bids_validation_report(tmp_path)


def test_error_findings_are_available_without_becoming_success(tmp_path):
    document = upstream_report({"code": "UNKNOWN_ERROR", "severity": "error"})
    with (
        patch("bids_repair.validation._validator_executable", return_value="validator"),
        patch(
            "bids_repair.validation.subprocess.run",
            return_value=Mock(returncode=1, stdout=json.dumps(document), stderr=""),
        ),
    ):
        assert get_bids_validation_report(tmp_path)["datasets"][0]["errors"] == 1
        with pytest.raises(RuntimeError, match="validation failed"):
            validate_bids_dataset(tmp_path)


def test_native_header_checks_are_not_silently_disabled(tmp_path):
    import nibabel as nib
    import numpy as np

    (tmp_path / "dataset_description.json").write_text(
        json.dumps(
            {
                "Name": "Header fixture",
                "BIDSVersion": "1.11.1",
                "Authors": ["Author"],
            }
        )
    )
    image = tmp_path / "sub-01" / "anat" / "sub-01_T1w.nii.gz"
    image.parent.mkdir(parents=True)
    nib.save(
        nib.Nifti1Image(np.zeros((2, 2, 2, 2), dtype=np.float32), np.eye(4)),
        image,
    )
    report = get_bids_validation_report(tmp_path)
    assert any(
        issue["code"] == "T1W_FILE_WITH_TOO_MANY_DIMENSIONS"
        for issue in report["datasets"][0]["issues"]
    )


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    directory = tmp_path / "bundle"
    directory.mkdir()
    name = "bids-validator.exe" if os.name == "nt" else "bids-validator"
    binary = directory / name
    binary.write_bytes(b"executable fixture")
    binary.chmod(0o755)
    schema = directory / "schema.json"
    schema.write_text('{"bids_version":"1.11.1","schema_version":"1.2.1"}')
    provenance = {
        "source_commit": native.SOURCE_COMMIT,
        "rust_package_version": native.RUST_VERSION,
        "upstream_version": native.REFERENCE_VERSION,
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "schema_sha256": hashlib.sha256(schema.read_bytes()).hexdigest(),
    }
    (directory / "provenance.json").write_text(json.dumps(provenance))
    monkeypatch.setattr(native, "PACKAGE_BIN", directory)
    return binary


def test_bundle_wins_without_path_lookup_or_subprocess(bundle):
    with (
        patch("bids_repair.native.shutil.which") as which,
        patch("bids_repair.native.subprocess.run") as run,
    ):
        assert native.validator_executable() == str(bundle)
    which.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize("damage", ["delete", "checksum", "provenance", "schema"])
def test_damaged_bundle_never_falls_back(bundle, damage):
    if damage == "delete":
        bundle.unlink()
    elif damage == "checksum":
        bundle.write_bytes(b"corrupt")
    elif damage == "schema":
        (bundle.parent / "schema.json").write_text("{}")
    else:
        (bundle.parent / "provenance.json").write_text("{}")
    with (
        patch("bids_repair.native.shutil.which") as which,
        pytest.raises(RuntimeError, match="damaged"),
    ):
        native.validator_executable()
    which.assert_not_called()


def test_missing_release_bundle_does_not_use_external(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "PACKAGE_BIN", tmp_path / "missing")
    monkeypatch.setattr(native, "source_root", lambda: None)
    with (
        patch("bids_repair.native.shutil.which") as which,
        pytest.raises(RuntimeError, match="reinstall"),
    ):
        native.validator_executable()
    which.assert_not_called()


def test_development_external_identity_is_verified(tmp_path, monkeypatch):
    executable = tmp_path / "bids-validator"
    executable.write_bytes(b"fixture")
    executable.chmod(0o755)
    monkeypatch.setattr(native, "PACKAGE_BIN", tmp_path / "missing")
    monkeypatch.setattr(native, "source_root", lambda: tmp_path)
    expected = (
        f"bids-validator {native.REFERENCE_VERSION} commit {native.REFERENCE_COMMIT[:7]} "
        f"rust {native.RUST_VERSION}\nrust git commit {native.SOURCE_COMMIT}\n"
    )
    with (
        patch("bids_repair.native.shutil.which", return_value=str(executable)),
        patch(
            "bids_repair.native.subprocess.run",
            return_value=Mock(returncode=0, stdout=expected, stderr=""),
        ) as run,
    ):
        assert native.validator_executable() == str(executable)
        run.return_value.stdout = "bids-validator from Deno"
        with pytest.raises(RuntimeError, match="Unexpected validator identity"):
            native.validator_executable()


def test_workspace_image_builds_pinned_upstream():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / ".devcontainer/Dockerfile").read_text()
    assert "FROM rust:1.90.0-bookworm AS validator" in dockerfile
    assert "tools/build_validator.py --output /build/validator" in dockerfile
    assert "tools/bids-validator-source.json" in dockerfile
    assert (
        json.loads((root / "tools/bids-validator-source.json").read_text())[
            "source_commit"
        ]
        == native.SOURCE_COMMIT
    )
    assert "/usr/local/share/doc/bids-validator/" in dockerfile
    assert "/usr/local/bin/bids-validator" in dockerfile
    assert "npm" not in dockerfile and "deno" not in dockerfile


def test_neurobagel_validation_gate_uses_rust_instead_of_pybids(tmp_path):
    import bagel.cli as bagel_cli

    from npdb.providers.neurobagel import BagelMixin

    report = {"datasets": [{"errors": 0}]}
    provider = BagelMixin(Mock(root=str(tmp_path)))

    def invoke(arguments):
        assert arguments[0] == "bids2tsv"
        assert bagel_cli.BIDSLayout(tmp_path, validate=True) == report
        return Mock(exit_code=0)

    provider.cli = Mock()
    provider.cli.invoke.side_effect = lambda app, arguments: invoke(arguments)
    with (
        patch(
            "npdb.providers.neurobagel.validate_and_repair_bids_dataset",
            return_value={**report, "repair": {"final_report": report}},
        ) as validation,
        patch("bagel.cli.BIDSLayout") as original_layout,
    ):
        provider.bids2tsv(str(tmp_path), str(tmp_path / "bids.tsv"))
        assert bagel_cli.BIDSLayout is original_layout
    validation.assert_called_once_with(str(tmp_path), severity_threshold="warning")
    original_layout.assert_not_called()

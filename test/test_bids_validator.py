import json
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from npdb.external.bids import validate_bids_dataset


def test_uses_only_the_rust_validator(tmp_path):
    report = {"datasets": [{"errors": 0, "warnings": 1}]}
    with (
        patch(
            "npdb.external.bids.shutil.which", return_value="/tools/bids-validator-rust"
        ) as which,
        patch(
            "npdb.external.bids.subprocess.run",
            return_value=Mock(returncode=0, stdout=json.dumps(report)),
        ) as run,
    ):
        assert validate_bids_dataset(tmp_path) == report
    which.assert_called_once_with("bids-validator-rust")
    run.assert_called_once_with(
        ["/tools/bids-validator-rust", str(tmp_path.resolve())],
        capture_output=True,
        text=True,
        check=False,
    )


def test_missing_rust_validator_does_not_fall_back(tmp_path):
    with (
        patch("npdb.external.bids.shutil.which", return_value=None),
        patch("npdb.external.bids.sys.executable", str(tmp_path / "python")),
        patch("npdb.external.bids.subprocess.run") as run,
        pytest.raises(RuntimeError, match="install_bids_validator.sh"),
    ):
        validate_bids_dataset(tmp_path)
    run.assert_not_called()


def test_finds_rust_validator_beside_interpreter(tmp_path):
    executable = tmp_path / "bids-validator-rust"
    executable.write_text("binary fixture")
    executable.chmod(0o755)
    report = {"datasets": [{"errors": 0}]}
    with (
        patch("npdb.external.bids.shutil.which", return_value=None),
        patch("npdb.external.bids.sys.executable", str(tmp_path / "python")),
        patch(
            "npdb.external.bids.subprocess.run",
            return_value=Mock(returncode=0, stdout=json.dumps(report)),
        ) as run,
    ):
        assert validate_bids_dataset(tmp_path) == report
    assert run.call_args.args[0][0] == str(executable)


def test_installer_reuses_existing_rust_binary_without_cargo(tmp_path):
    source = tmp_path / "bin"
    source.mkdir()
    executable = source / "bids-validator-rust"
    executable.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'bids-validator-rust 0.0.3 (bids-validate 0.0.3)'\n"
    )
    executable.chmod(0o755)
    prefix = tmp_path / "environment"
    installer = (
        Path(__file__).resolve().parents[1] / "scripts/install_bids_validator.sh"
    )
    result = subprocess.run(
        ["bash", str(installer), str(prefix)],
        env={**os.environ, "PATH": f"{source}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "already installed" in result.stdout
    assert (prefix / "bin/bids-validator-rust").read_bytes() == executable.read_bytes()
    assert os.access(prefix / "bin/bids-validator-rust", os.X_OK)


def test_workspace_image_includes_only_native_validation_tools():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / ".devcontainer/Dockerfile").read_text()
    assert "FROM rust:1.90.0-bookworm AS validator" in dockerfile
    assert "cargo install --locked" in dockerfile
    assert "apt-get install -y --no-install-recommends dcm2niix" in dockerfile
    assert "bids-validator-rust /usr/local/bin/bids-validator-rust" in dockerfile
    assert "npm" not in dockerfile
    assert "deno" not in dockerfile
    ignored = (root / ".devcontainer/Dockerfile.dockerignore").read_text().splitlines()
    assert ignored[0] == "**"
    assert "!tools/bids-validator-rust/Cargo.lock" in ignored
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
            "npdb.managers.neurobagel.validate_bids_dataset", return_value=report
        ) as validation,
        patch("bagel.cli.BIDSLayout") as original_layout,
    ):
        manager.bids2tsv(str(tmp_path), str(tmp_path / "bids.tsv"))
        assert bagel_cli.BIDSLayout is original_layout
    validation.assert_called_once_with(str(tmp_path))
    original_layout.assert_not_called()


@pytest.mark.parametrize(
    "code, stdout",
    [
        (1, "validation failed"),
        (0, "not json"),
        (0, '{"datasets": [{"errors": 1}]}'),
        (0, '{"datasets": []}'),
    ],
)
def test_rejects_failed_or_invalid_validator_results(tmp_path, code, stdout):
    with (
        patch(
            "npdb.external.bids.shutil.which", return_value="/tools/bids-validator-rust"
        ),
        patch(
            "npdb.external.bids.subprocess.run",
            return_value=Mock(returncode=code, stdout=stdout, stderr="error"),
        ),
        pytest.raises(RuntimeError),
    ):
        validate_bids_dataset(tmp_path)

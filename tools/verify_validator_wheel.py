"""Inspect a wheel, install it from a local wheelhouse, and validate offline."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def verify(wheel: Path, wheelhouse: Path) -> dict:
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")
        )
        metadata = archive.read(metadata_name).decode()
        if "Root-Is-Purelib: false" not in metadata or "Tag: py3-none-any" in metadata:
            raise RuntimeError(
                "The validator wheel must be platform-specific and non-purelib."
            )
        binaries = [
            name
            for name in archive.namelist()
            if name
            in {
                "bids_repair/_bin/bids-validator",
                "bids_repair/_bin/bids-validator.exe",
            }
        ]
        if len(binaries) != 1:
            raise RuntimeError("Expected exactly one bundled validator.")
        binary = archive.getinfo(binaries[0])
        if (
            not binaries[0].endswith(".exe")
            and not (binary.external_attr >> 16) & 0o111
        ):
            raise RuntimeError("The bundled Unix validator is not executable.")
        for name in (
            "LICENSE",
            "THIRD-PARTY-LICENSES",
            "schema.json",
            "provenance.json",
        ):
            archive.getinfo(f"bids_repair/_bin/{name}")
        provenance = json.loads(archive.read("bids_repair/_bin/provenance.json"))
        metrics = {
            "wheel": wheel.name,
            "wheel_bytes": wheel.stat().st_size,
            "executable_bytes": binary.file_size,
            "compressed_executable_bytes": binary.compress_size,
            "target": provenance["target"],
        }

    with tempfile.TemporaryDirectory(prefix="npdb-wheel-check-") as temporary:
        root = Path(temporary)
        environment = root / "environment"
        subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
        python = environment / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheelhouse.resolve()),
                str(wheel.resolve()),
            ],
            check=True,
        )
        dataset = root / "dataset with spaces"
        dataset.mkdir()
        (dataset / "dataset_description.json").write_text(
            json.dumps(
                {
                    "Name": "Offline wheel fixture",
                    "BIDSVersion": provenance["bids_version"],
                    "Authors": ["Fixture Author"],
                }
            )
        )
        smoke = r"""
import json, shutil, socket, sys
from pathlib import Path
def no_network(*args, **kwargs):
    raise AssertionError("Network access is forbidden in this offline smoke test")
socket.create_connection = no_network
socket.socket.connect = no_network
from bids_repair.native import validator_executable
from bids_repair.validation import get_bids_validation_report
from npdb.external.bids import get_bids_validation_report as alias
assert alias is get_bids_validation_report
for tool in ("cargo", "rustc", "node", "deno", "bids-validator", "bids-validator-rust"):
    assert shutil.which(tool) is None, tool
executable = Path(validator_executable())
assert executable.parent.name == "_bin", executable
report = get_bids_validation_report(sys.argv[1], target_version=sys.argv[2])
assert report["datasets"][0]["errors"] == 0, report
root = Path(sys.argv[1])
derivative = root / "derivatives" / "pipeline"
derivative.mkdir(parents=True)
report = get_bids_validation_report(root)
assert len(report["datasets"]) == 2, report
assert any(issue["code"] == "DERIVATIVE_PROVENANCE" for issue in report["datasets"][1]["issues"])
(derivative / "dataset_description.json").write_text(json.dumps({
    "Name": "Derivative fixture", "BIDSVersion": sys.argv[2],
    "DatasetType": "derivative", "GeneratedBy": [{"Name": "fixture"}],
    "Authors": ["Fixture Author"],
}))
report = get_bids_validation_report(root)
assert all(item["errors"] == 0 for item in report["datasets"]), report
(root / "dataset_description.json").write_text("{")
report = get_bids_validation_report(root)
assert report["datasets"][0]["errors"] > 0, report
assert any(issue["code"] == "JSON_INVALID" for issue in report["datasets"][0]["issues"])
print("Installed-wheel offline API smoke passed")
"""
        env = {
            **os.environ,
            "PATH": str(environment / "empty-path"),
            "PYTHONPATH": "",
            "XDG_CACHE_HOME": str(root / "empty-cache"),
        }
        subprocess.run(
            [str(python), "-I", "-c", smoke, str(dataset), provenance["bids_version"]],
            env=env,
            cwd=root,
            check=True,
        )
        (dataset / "dataset_description.json").write_text(
            json.dumps(
                {
                    "Name": "Offline wheel fixture",
                    "BIDSVersion": provenance["bids_version"],
                    "Authors": ["Fixture Author"],
                }
            )
        )
        cli = r"""
import socket
def no_network(*args, **kwargs):
    raise AssertionError("Network access is forbidden")
socket.create_connection = no_network
socket.socket.connect = no_network
from bids_repair.cli import app
app()
"""
        result = subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                cli,
                "validate",
                str(dataset),
                "--bids-version",
                provenance["bids_version"],
                "--format",
                "json",
            ],
            env=env,
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(result.stdout)["validation"]["datasets"][0]["errors"] == 0
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--metrics", type=Path)
    arguments = parser.parse_args()
    metrics = verify(arguments.wheel, arguments.wheelhouse)
    text = json.dumps(metrics, indent=2) + "\n"
    print(text)
    if arguments.metrics:
        arguments.metrics.write_text(text, encoding="utf-8")

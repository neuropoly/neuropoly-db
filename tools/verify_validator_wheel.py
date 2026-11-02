"""Install a complete local wheelhouse and exercise the bundled validator offline."""

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
        metadata = archive.read(next(
            name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")
        )).decode()
        if "Root-Is-Purelib: false" not in metadata or "Tag: py3-none-any" in metadata:
            raise RuntimeError("The native wheel must be platform-specific and non-purelib.")
        binaries = [name for name in archive.namelist() if name in {
            "npdb/_bin/bids-validator", "npdb/_bin/bids-validator.exe",
        }]
        if len(binaries) != 1:
            raise RuntimeError("Expected exactly one bundled native validator.")
        binary = archive.getinfo(binaries[0])
        if not binaries[0].endswith(".exe") and not (binary.external_attr >> 16) & 0o111:
            raise RuntimeError("The bundled Unix executable lacks executable permissions.")
        for name in ("schema.json", "LICENSE", "THIRD-PARTY-LICENSES", "provenance.json"):
            archive.getinfo(f"npdb/_bin/{name}")
        provenance = json.loads(archive.read("npdb/_bin/provenance.json"))
        metrics = {
            "wheel": wheel.name, "wheel_bytes": wheel.stat().st_size,
            "executable_bytes": binary.file_size,
            "compressed_executable_bytes": binary.compress_size,
            "target": provenance["target"],
        }
    with tempfile.TemporaryDirectory(prefix="npdb-offline-wheel-") as temporary:
        root = Path(temporary)
        environment = root / "environment"
        subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([
            str(python), "-m", "pip", "install", "--no-index",
            "--find-links", str(wheelhouse.resolve()), str(wheel.resolve()),
        ], check=True)
        dataset = root / "dataset with spaces"
        dataset.mkdir()
        (dataset / "dataset_description.json").write_text(json.dumps({
            "Name": "Offline fixture", "BIDSVersion": provenance["bids_version"],
            "Authors": ["Fixture Author"],
        }), encoding="utf-8")
        smoke = r"""
import json, shutil, socket, sys
from pathlib import Path
def no_network(*args, **kwargs):
    raise AssertionError("Network is forbidden in installed-wheel acceptance")
socket.create_connection = no_network
socket.socket.connect = no_network
from npdb.external.native import validator_executable
from npdb.external.bids import get_bids_validation_report, validate_bids_dataset
from npdb.bids_repair import plan_bids_repair
from npdb.cli.cli import npdb
from typer.testing import CliRunner
assert "bagel.cli" not in sys.modules, "Standalone validation must not import networked Bagel mappings"
for tool in ("cargo", "rustc", "node", "deno", "bids-validator", "bids-validator-rust"):
    assert shutil.which(tool) is None, tool
assert Path(validator_executable()).parent.name == "_bin"
root = Path(sys.argv[1])
assert get_bids_validation_report(root)["datasets"][0]["errors"] == 0
assert CliRunner().invoke(npdb, ["--help"]).exit_code == 0
assert CliRunner().invoke(npdb, ["repair", "bids", str(root), "--dry-run"]).exit_code == 0
child = root / "derivatives" / "pipeline"
child.mkdir(parents=True)
# Upstream recursion intentionally ignores directories without a description.
assert len(get_bids_validation_report(root)["datasets"]) == 1
(child / "dataset_description.json").write_text(json.dumps({
    "Name": "Derivative", "BIDSVersion": sys.argv[2], "DatasetType": "derivative",
    "GeneratedBy": [{"Name": "fixture"}], "Authors": ["Fixture Author"],
}))
report = get_bids_validation_report(root)
assert len(report["datasets"]) == 2, report
assert report["datasets"][1]["dataset"] == str(child), report
(child / "dataset_description.json").write_text("{")
report = validate_bids_dataset(root)
assert report["datasets"][1]["errors"] > 0, report
(root / "dataset_description.json").write_text("{")
report = get_bids_validation_report(root)
assert report["datasets"][0]["errors"] > 0, report
assert any(issue["code"] == "JSON_INVALID" for issue in report["datasets"][0]["issues"])
assert plan_bids_repair(root)["operations"] == []
try:
    validate_bids_dataset(root)
except RuntimeError:
    pass
else:
    raise AssertionError("Raw validation errors must fail the severity gate")
print("Installed-wheel offline validation and CLI acceptance passed")
"""
        subprocess.run(
            [str(python), "-I", "-c", smoke, str(dataset), provenance["bids_version"]],
            cwd=root, check=True,
            env={**os.environ, "PATH": str(root / "empty-path"), "PYTHONPATH": "",
                 "XDG_CACHE_HOME": str(root / "empty-cache")},
        )
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--metrics", type=Path)
    args = parser.parse_args()
    text = json.dumps(verify(args.wheel, args.wheelhouse), indent=2) + "\n"
    print(text)
    if args.metrics:
        args.metrics.write_text(text, encoding="utf-8")

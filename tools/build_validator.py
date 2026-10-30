"""Build and stage the pinned upstream executable for wheels and development."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def source_directory(root: Path) -> Path:
    source = root / "tools/bids-validator-rust"
    if not (source / "Cargo.toml").is_file():
        raise RuntimeError(
            "The pinned Rust validator source is missing. From a Git checkout run "
            "'git submodule update --init --recursive -- tools/bids-validator-rust', "
            "or use the npdb source distribution (sdist), not a GitHub source archive."
        )
    return source


def build_bundle(root: Path, destination: Path, target: str | None = None) -> Path:
    source = source_directory(root)
    cargo = shutil.which("cargo")
    if cargo is None:
        raise RuntimeError(
            "Building npdb from source requires Rust 1.90+ with Cargo. "
            "Install a prebuilt npdb wheel to avoid the Rust toolchain."
        )
    pin = json.loads((root / "tools/bids-validator-source.json").read_text())
    environment = {
        **os.environ,
        "BIDS_VALIDATOR_GIT_COMMIT": pin["source_commit"],
        "BIDS_VALIDATOR_UPSTREAM_VERSION": pin["upstream_version"],
        "BIDS_VALIDATOR_UPSTREAM_COMMIT": pin["upstream_commit"],
    }
    if (target and "windows" in target) or (target is None and sys.platform == "win32"):
        environment["RUSTFLAGS"] = (
            environment.get("RUSTFLAGS", "") + " -C target-feature=+crt-static"
        ).strip()
    command = [
        cargo,
        "build",
        "--release",
        "--locked",
        "--manifest-path",
        str(source / "Cargo.toml"),
    ]
    if target:
        command.extend(["--target", target])
    subprocess.run(command, env=environment, check=True)
    metadata = subprocess.run(
        [
            cargo,
            "metadata",
            "--locked",
            "--format-version",
            "1",
            "--manifest-path",
            str(source / "Cargo.toml"),
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    packages = json.loads(metadata.stdout)
    if target is None:
        host = subprocess.run(
            [cargo, "-vV"], check=True, capture_output=True, text=True
        ).stdout
        target = next(
            line.removeprefix("host: ")
            for line in host.splitlines()
            if line.startswith("host: ")
        )
        binary_directory = Path(packages["target_directory"]) / "release"
    else:
        binary_directory = Path(packages["target_directory"]) / target / "release"
    name = "bids-validator.exe" if "windows" in target else "bids-validator"
    destination.mkdir(parents=True, exist_ok=True)
    executable = destination / name
    shutil.copy2(binary_directory / name, executable)
    executable.chmod(0o755)
    schema = source / "schemas/schema-1.2.1.json"
    shutil.copy2(schema, destination / "schema.json")
    schema_data = json.loads(schema.read_text())
    provenance = {
        **pin,
        "target": target,
        "schema_version": schema_data["schema_version"],
        "bids_version": schema_data["bids_version"],
        "schema_sha256": hashlib.sha256(schema.read_bytes()).hexdigest(),
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
    }
    (destination / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    shutil.copy2(source / "LICENSE", destination / "LICENSE")
    notices = [(root / "tools/bids-schema-NOTICE").read_text(encoding="utf-8")]
    for package in sorted(packages["packages"], key=lambda item: item["id"]):
        notices.append(
            f"\n=== {package['name']} {package['version']} "
            f"({package.get('license') or 'see source license'}) ===\n"
        )
        directory = Path(package["manifest_path"]).parent
        for license_file in sorted(directory.iterdir()):
            if license_file.is_file() and license_file.name.upper().startswith(
                ("LICENSE", "COPYING", "NOTICE")
            ):
                notices.append(license_file.read_text(encoding="utf-8"))
    (destination / "THIRD-PARTY-LICENSES").write_text(
        "\n".join(notices), encoding="utf-8"
    )
    return executable


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target")
    arguments = parser.parse_args()
    try:
        executable = build_bundle(
            Path(__file__).resolve().parents[1], arguments.output, arguments.target
        )
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"{exc}\n")
    print(executable)

"""Build upstream unchanged; all Cargo outputs and bundles belong to npdb."""

import argparse
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def native_helpers(root: Path) -> dict:
    return runpy.run_path(str(root / "src/npdb/external/native.py"))


def source_files(root: Path) -> list[Path]:
    source = root / "tools/bids-validator-rust"
    files = [source / name for name in ("Cargo.toml", "Cargo.lock", "build.rs", "LICENSE")]
    for name in ("src", "crates", "schemas", "benches"):
        files.extend(
            path
            for path in (source / name).rglob("*")
            if path.is_file()
            and not set(path.relative_to(source).parts) & {"target", ".git", "__pycache__"}
        )
    return sorted(files)


def source_manifest(root: Path) -> dict[str, str]:
    source = root / "tools/bids-validator-rust"
    # Git can materialize CRLF on Windows. Fingerprint canonical text, never rewrite it.
    return {
        path.relative_to(source).as_posix(): hashlib.sha256(
            path.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for path in source_files(root)
    }


def source_directory(root: Path) -> Path:
    source = root / "tools/bids-validator-rust"
    if not all(
        (source / name).is_file()
        for name in ("Cargo.toml", "Cargo.lock", "build.rs", "schemas/schema-1.2.1.json")
    ):
        raise RuntimeError(
            "Pinned validator source is missing. Initialize only the GitHub submodule "
            "with 'git submodule update --init --recursive -- tools/bids-validator-rust', "
            "or build from an npdb sdist (GitHub source archives omit submodules)."
        )
    identity = native_helpers(root)["source_identity"]()
    actual = source_manifest(root)
    digest = hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest()
    if digest != identity.get("source_tree_sha256"):
        raise RuntimeError("Validator source files do not match npdb's pinned source digest.")
    if (source / ".git").exists():
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(source), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        )
        if result.stdout.strip() != identity["source_commit"]:
            raise RuntimeError("Validator submodule is not at npdb's pinned revision.")
        status = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(source), "status", "--porcelain"],
            check=True, capture_output=True, text=True,
        )
        if status.stdout:
            raise RuntimeError("Validator submodule has local changes; refusing to build.")
    manifest = root / "tools/validator-source-files.json"
    if manifest.is_file():
        expected = json.loads(manifest.read_text(encoding="utf-8"))
        if actual != expected:
            raise RuntimeError("Validator sdist sources do not match their source manifest.")
    return source


def build_bundle(root: Path, destination: Path, target: str | None = None) -> Path:
    source = source_directory(root).resolve()
    destination = destination.resolve()
    cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")).resolve()
    if destination.is_relative_to(source) or cargo_home.is_relative_to(source):
        raise RuntimeError("Build destinations and CARGO_HOME must be outside the submodule.")
    identity = native_helpers(root)["source_identity"]()
    artifacts = runpy.run_path(str(root / "tools/validator_artifacts.py"))
    artifact_target = target or artifacts["host_target"]()
    acquired = artifacts["acquire"](root, destination, artifact_target)
    if acquired is not None:
        executable, provenance = acquired
        finish_bundle(source, destination, executable, artifact_target, identity, provenance)
        source_directory(root)
        return executable
    host = artifacts["host_target"]()
    if artifact_target != host and not (
        host.endswith("-unknown-linux-gnu")
        and artifact_target == "x86_64-pc-windows-msvc"
        and os.environ.get("NPDB_CARGO_XWIN") == "1"
    ):
        raise RuntimeError(
            f"No usable prebuilt validator for {artifact_target}; source fallback cannot "
            f"build this target on {host}. Use a matching native host, or enable "
            "cargo-xwin for a Windows build from Linux."
        )
    cargo = shutil.which("cargo")
    if cargo is None:
        raise RuntimeError(
            "Building npdb from source requires Rust 1.90+ with Cargo. "
            "Install a prebuilt platform wheel to avoid compiling Rust."
        )
    output = root.resolve() / "build" / "cargo-validator"
    if output.resolve().is_relative_to(source):
        raise RuntimeError("The parent build directory must not point into the submodule.")
    if Path(os.environ.get("RUSTUP_HOME", Path.home() / ".rustup")).resolve().is_relative_to(source):
        raise RuntimeError("RUSTUP_HOME must be outside the validator submodule.")
    environment = {
        **os.environ,
        "CARGO_TARGET_DIR": str(output),
        "BIDS_VALIDATOR_GIT_COMMIT": identity["source_commit"],
        "BIDS_VALIDATOR_UPSTREAM_VERSION": identity["upstream_version"],
        "BIDS_VALIDATOR_UPSTREAM_COMMIT": identity["upstream_commit"],
    }
    if target is None:
        host = subprocess.run(
            [cargo, "-vV"], check=True, capture_output=True, text=True
        ).stdout
        target = next(line.removeprefix("host: ") for line in host.splitlines()
                      if line.startswith("host: "))
    cross_compile_windows = os.environ.get("NPDB_CARGO_XWIN") == "1"
    if cross_compile_windows and target != "x86_64-pc-windows-msvc":
        raise RuntimeError(
            "NPDB_CARGO_XWIN can only build the x86_64-pc-windows-msvc target."
        )
    if "windows" in target:
        environment["RUSTFLAGS"] = (
            environment.get("RUSTFLAGS", "") + " -C target-feature=+crt-static"
        ).strip()
    build_command = [cargo, "build"]
    if cross_compile_windows:
        if shutil.which("cargo-xwin") is None:
            raise RuntimeError(
                "Cross-building Windows MSVC wheels requires cargo-xwin and Clang/LLVM. "
                "Install cargo-xwin and accept its Windows SDK/CRT license terms."
            )
        build_command = [cargo, "xwin", "build"]
    subprocess.run(
        [*build_command, "--release", "--locked", "--bin", "bids-validator",
         "--manifest-path", str(source / "Cargo.toml"), "--target-dir", str(output),
         "--target", target],
        env=environment, check=True,
    )
    metadata = subprocess.run(
        [cargo, "metadata", "--locked", "--format-version", "1",
         "--manifest-path", str(source / "Cargo.toml"), "--filter-platform", target],
        env=environment, check=True, capture_output=True, text=True,
    )
    packages = json.loads(metadata.stdout)["packages"]
    name = "bids-validator.exe" if "windows" in target else "bids-validator"
    destination.mkdir(parents=True, exist_ok=True)
    executable = destination / name
    shutil.copy2(output / target / "release" / name, executable)
    executable.chmod(0o755)
    finish_bundle(source, destination, executable, target, identity, {"acquisition": "source"})
    (destination / "THIRD-PARTY-LICENSES").write_text(third_party_notices(packages), encoding="utf-8")
    source_directory(root)
    return executable


def finish_bundle(
    source: Path, destination: Path, executable: Path,
    target: str, identity: dict, acquisition: dict,
) -> None:
    schema = source / "schemas/schema-1.2.1.json"
    shutil.copy2(schema, destination / "schema.json")
    provenance = {
        **identity, **acquisition, "target": target,
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "schema_sha256": hashlib.sha256(schema.read_bytes()).hexdigest(),
    }
    (destination / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8",
    )
    shutil.copy2(source / "LICENSE", destination / "LICENSE")


def third_party_notices(packages: list[dict]) -> str:
    notices = [
        "Bundled BIDS schema 1.2.1 (BIDS 1.11.1): CC-BY-4.0.\n"
        "Source: https://github.com/bids-standard/bids-specification/tree/v1.11.1\n"
        "License: https://creativecommons.org/licenses/by/4.0/\n"
        "The native engine embeds upstream schema patches; see the pinned upstream source.\n"
    ]
    for package in sorted(packages, key=lambda item: item["id"]):
        notices.append(f"\n=== {package['name']} {package['version']} "
                       f"({package.get('license') or 'see source license'}) ===\n")
        for path in sorted(Path(package["manifest_path"]).parent.iterdir()):
            if path.is_file() and path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                notices.append(path.read_text(encoding="utf-8"))
    return "\n".join(notices)


def install(root: Path, prefix: Path) -> None:
    helpers = native_helpers(root)
    bundle = prefix.resolve() / ("Scripts" if os.name == "nt" else "bin") / "npdb-validator"
    if bundle.resolve().is_relative_to((root / "tools/bids-validator-rust").resolve()):
        raise RuntimeError("The installation prefix must be outside the validator submodule.")
    candidates = [bundle, Path("/usr/local/bin/npdb-validator")]
    for candidate in dict.fromkeys(candidates):
        if candidate.exists():
            try:
                executable = helpers["verify_bundle"](candidate)
            except RuntimeError as exc:
                print(f"{exc}; rebuilding the pinned bundle instead.", file=sys.stderr)
                continue
            if candidate != bundle:
                shutil.copytree(candidate, bundle, dirs_exist_ok=True)
            print(f"Pinned Rust BIDS validator already installed: {executable}")
            break
    else:
        build_bundle(root, bundle)
    executable = helpers["verify_bundle"](bundle)
    shutil.copy2(executable, bundle.parent / executable.name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--install", type=Path)
    parser.add_argument("--target")
    args = parser.parse_args()
    try:
        if args.install:
            install(ROOT, args.install)
        else:
            print(build_bundle(ROOT, args.output, args.target))
    except subprocess.CalledProcessError as exc:
        diagnostics = [str(exc), exc.stdout, exc.stderr]
        parser.exit(1, "\n".join(part.rstrip() for part in diagnostics if part) + "\n")
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"{exc}\n")

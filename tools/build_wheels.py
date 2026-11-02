"""Build selected wheels using approved artifacts, with native/Cargo fallbacks."""

import argparse
import os
import platform
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "linux-x86_64": ("x86_64-unknown-linux-gnu", "manylinux_2_28_x86_64"),
    "linux-arm64": ("aarch64-unknown-linux-gnu", "manylinux_2_28_aarch64"),
    "macos-x86_64": ("x86_64-apple-darwin", "macosx_11_0_x86_64"),
    "macos-arm64": ("aarch64-apple-darwin", "macosx_11_0_arm64"),
    "windows-x86_64": ("x86_64-pc-windows-msvc", "win_amd64"),
}
BOOTSTRAP = """
set -eu
export PATH=/opt/python/cp312-cp312/bin:/root/.cargo/bin:$PATH
python -m pip install uv
curl --fail --proto '=https' --tlsv1.2 https://sh.rustup.rs -o /tmp/rustup.sh
sh /tmp/rustup.sh -y --profile minimal --default-toolchain 1.90.0
exec python tools/build_wheels.py "$@"
"""


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", subprocess.list2cmdline(command), flush=True)
    try:
        return subprocess.run(command, check=True, **kwargs)
    except subprocess.CalledProcessError as error:
        if error.stdout:
            print(error.stdout, file=sys.stderr)
        if error.stderr:
            print(error.stderr, file=sys.stderr)
        raise


def host_target() -> str:
    system = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}.get(
        platform.system(), platform.system().lower()
    )
    machine = platform.machine().lower()
    architecture = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    return f"{system}-{architecture}"


def source_paths(root: Path) -> list[Path]:
    helpers = runpy.run_path(str(root / "tools/build_validator.py"))
    helpers["source_directory"](root)
    paths = [
        root / name for name in (
            "pyproject.toml", "uv.lock", "hatch_build.py", "README.md",
            "docs/install.md", "scripts/install_bids_validator.sh",
        )
    ]
    paths.extend((root / "tools").glob("*.py"))
    paths.extend(
        path for path in (root / "src").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    )
    paths.extend(helpers["source_files"](root))
    return sorted(set(paths))


def build(
    target: str, output: Path, verify: bool, *, cross_compile_windows: bool = False,
    source_only: bool = False,
) -> None:
    rust_target, tag = TARGETS[target]
    environment = {
        **os.environ, "NPDB_RUST_TARGET": rust_target, "NPDB_WHEEL_PLATFORM": tag,
    }
    if cross_compile_windows:
        environment["NPDB_CARGO_XWIN"] = "1"
    if source_only:
        environment["NPDB_VALIDATOR_SOURCE_ONLY"] = "1"
    if target.startswith("macos-"):
        environment["MACOSX_DEPLOYMENT_TARGET"] = "11.0"
    output.mkdir(parents=True, exist_ok=True)
    run(["uv", "build", "--sdist", "--out-dir", str(output)], cwd=ROOT, env=environment)
    run(["uv", "build", "--wheel", "--out-dir", str(output)], cwd=ROOT, env=environment)
    if verify:
        wheel, = output.glob("*.whl")
        run([
            sys.executable, "-m", "pip", "wheel", "--prefer-binary",
            "--wheel-dir", str(output / "wheelhouse"), str(wheel),
        ], cwd=ROOT)


def verify_wheel(output: Path) -> None:
    wheel, = output.glob("*.whl")
    run([
        sys.executable, str(ROOT / "tools/verify_validator_wheel.py"), str(wheel),
        "--wheelhouse", str(output / "wheelhouse"),
        "--metrics", str(output / "validator-sizes.json"),
    ])


def container_run(
    image: str, docker_platform: str, command: list[str],
    project: Path, output: Path, *, offline: bool = False,
) -> None:
    create = [
        "docker", "create", "--platform", docker_platform, "--workdir", "/project",
    ]
    if offline:
        create.extend(["--network", "none"])
    result = run(create + [image, *command], capture_output=True, text=True)
    container = result.stdout.strip()
    try:
        run(["docker", "cp", str(project) + "/.", f"{container}:/project"])
        if offline:
            run(["docker", "cp", str(output) + "/.", f"{container}:/output"])
        run(["docker", "start", "--attach", container])
        status = run(
            ["docker", "inspect", "--format", "{{.State.ExitCode}}", container],
            capture_output=True, text=True,
        )
        if status.stdout.strip() != "0":
            raise RuntimeError(f"Wheel container exited with status {status.stdout.strip()}.")
        if not offline:
            run(["docker", "cp", f"{container}:/output/.", str(output)])
        else:
            run([
                "docker", "cp", f"{container}:/output/validator-sizes.json",
                str(output / "validator-sizes.json"),
            ])
    finally:
        run(["docker", "rm", "--force", container])


def build_linux(
    target: str, project: Path, output: Path, verify: bool, *, source_only: bool = False,
) -> None:
    tag = TARGETS[target][1]
    image = f"quay.io/pypa/{tag}"
    docker_platform = "linux/amd64" if target == "linux-x86_64" else "linux/arm64"
    worker = [
        "tools/build_wheels.py", "--targets", target, "--output", "/output",
    ]
    command = [
        "bash", "-c", BOOTSTRAP, "wheel-builder",
        *worker[1:], "--stage", "build",
    ]
    if verify:
        command.append("--verify")
    if source_only or os.environ.get("NPDB_VALIDATOR_SOURCE_ONLY") == "1":
        command.append("--source-only")
    output.mkdir(parents=True)
    container_run(image, docker_platform, command, project, output)
    if verify:
        container_run(
            image, docker_platform,
            ["/opt/python/cp312-cp312/bin/python", *worker, "--stage", "verify"],
            project, output, offline=True,
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--targets", nargs="+", choices=TARGETS,
        help="Space-separated targets; defaults to this machine's OS/architecture.",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "dist/wheels",
        help="Output directory (default: dist/wheels), with a subdirectory per target.",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Also prepare dependencies and test installed wheels offline (larger download).",
    )
    parser.add_argument(
        "--cross-compile-windows", action="store_true",
        help="Allow cargo-xwin source fallback for Windows from Linux; verify on Windows.",
    )
    parser.add_argument(
        "--source-only", action="store_true",
        help="Disable artifact acquisition and use the existing Rust build chain.",
    )
    parser.add_argument(
        "--verify-existing", action="store_true",
        help="Verify an existing wheel using the prepared wheelhouse on its native platform.",
    )
    parser.add_argument("--stage", choices=("build", "verify"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    targets = list(dict.fromkeys(args.targets or [host_target()]))
    if any(target not in TARGETS for target in targets):
        parser.error("Unsupported host; select an explicit --targets value.")
    if args.cross_compile_windows:
        if platform.system() != "Linux":
            parser.error("--cross-compile-windows is only supported from a Linux host.")
        if "windows-x86_64" not in targets:
            parser.error("--cross-compile-windows requires the windows-x86_64 target.")
    if args.verify and any(
        not target.startswith("linux-") and target != host_target() for target in targets
    ):
        parser.error(
            "Foreign-platform wheels cannot be verified on this host; build without "
            "--verify, then run --verify-existing on the target's native host."
        )
    output = args.output.resolve()
    if args.verify_existing:
        if (
            args.verify or args.cross_compile_windows or args.source_only
            or args.stage or len(targets) != 1
        ):
            parser.error(
                "--verify-existing requires exactly one target and cannot be combined "
                "with build options."
            )
        if targets[0] != host_target():
            parser.error(
                "--verify-existing must run on the target's native OS and architecture."
            )
        verify_wheel(output)
        return
    if args.stage:
        if len(targets) != 1:
            parser.error("Internal build stages require exactly one target.")
        if args.stage == "build":
            build(
                targets[0], output, args.verify,
                cross_compile_windows=args.cross_compile_windows,
                source_only=args.source_only,
            )
        else:
            verify_wheel(output)
        return
    if any(target.startswith("linux-") for target in targets):
        if shutil.which("docker") is None:
            parser.error("Install Docker and start its daemon to build Linux wheels.")
        run(["docker", "info"], stdout=subprocess.DEVNULL)
    if any(not target.startswith("linux-") for target in targets) and shutil.which("uv") is None:
        parser.error("Install uv before building desktop wheels (see the wiki).")
    paths = source_paths(ROOT)
    with tempfile.TemporaryDirectory(prefix="npdb-wheels-") as temporary:
        staging = Path(temporary)
        for target in targets:
            print(f"\n==> Building {target}", flush=True)
            project = staging / f"{target}-source"
            for path in paths:
                destination = project / path.relative_to(ROOT)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
            result = staging / target
            if target.startswith("linux-"):
                options = {"source_only": True} if args.source_only else {}
                build_linux(target, project, result, args.verify, **options)
            else:
                command = [
                    sys.executable, str(project / "tools/build_wheels.py"),
                    "--targets", target, "--output", str(result), "--stage", "build",
                ]
                if args.cross_compile_windows and target == "windows-x86_64":
                    command.append("--cross-compile-windows")
                if args.verify:
                    command.append("--verify")
                if args.source_only:
                    command.append("--source-only")
                run(command, env={
                    **os.environ,
                    "NPDB_VALIDATOR_CACHE": os.environ.get(
                        "NPDB_VALIDATOR_CACHE", str(ROOT / "build/artifact-cache"),
                    ),
                })
                if args.verify:
                    run([
                        sys.executable, str(project / "tools/build_wheels.py"),
                        "--targets", target, "--output", str(result), "--stage", "verify",
                    ])
            destination = output / target
            shutil.copytree(result, destination, dirs_exist_ok=True)
            print(f"==> Saved {target}: {destination}", flush=True)


if __name__ == "__main__":
    main()

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_wheels", ROOT / "tools/build_wheels.py")
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


@pytest.mark.parametrize(
    ("system", "machine", "target"),
    [
        ("Linux", "aarch64", "linux-arm64"),
        ("Linux", "x86_64", "linux-x86_64"),
        ("Darwin", "arm64", "macos-arm64"),
        ("Windows", "AMD64", "windows-x86_64"),
    ],
)
def test_host_target(monkeypatch, system, machine, target):
    monkeypatch.setattr(BUILDER.platform, "system", lambda: system)
    monkeypatch.setattr(BUILDER.platform, "machine", lambda: machine)
    assert BUILDER.host_target() == target


def test_rejects_foreign_windows_verification_before_running_any_commands(monkeypatch):
    monkeypatch.setattr(BUILDER, "host_target", lambda: "linux-x86_64")
    run = Mock()
    monkeypatch.setattr(BUILDER, "run", run)
    with pytest.raises(SystemExit, match="2"):
        BUILDER.main(["--targets", "windows-x86_64", "--verify"])
    run.assert_not_called()


def test_rejects_cross_windows_verification_on_linux(monkeypatch):
    monkeypatch.setattr(BUILDER.platform, "system", lambda: "Linux")
    run = Mock()
    monkeypatch.setattr(BUILDER, "run", run)
    with pytest.raises(SystemExit, match="2"):
        BUILDER.main([
            "--targets", "windows-x86_64", "--cross-compile-windows", "--verify",
        ])
    run.assert_not_called()


def test_rejects_existing_wheel_verification_on_wrong_host(monkeypatch):
    monkeypatch.setattr(BUILDER.platform, "system", lambda: "Windows")
    monkeypatch.setattr(BUILDER, "host_target", lambda: "linux-x86_64")
    verify = Mock()
    monkeypatch.setattr(BUILDER, "verify_wheel", verify)
    with pytest.raises(SystemExit, match="2"):
        BUILDER.main([
            "--targets", "windows-x86_64", "--verify-existing",
        ])
    verify.assert_not_called()


def test_verifies_existing_wheel_on_matching_host(monkeypatch, tmp_path):
    monkeypatch.setattr(BUILDER.platform, "system", lambda: "Windows")
    monkeypatch.setattr(BUILDER, "host_target", lambda: "windows-x86_64")
    verify = Mock()
    monkeypatch.setattr(BUILDER, "verify_wheel", verify)
    BUILDER.main([
        "--targets", "windows-x86_64", "--verify-existing",
        "--output", str(tmp_path),
    ])
    verify.assert_called_once_with(tmp_path.resolve())


def test_windows_cross_build_routes_through_staged_cross_compile(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("source")
    monkeypatch.setattr(BUILDER, "ROOT", source)
    monkeypatch.setattr(BUILDER, "host_target", lambda: "linux-x86_64")
    monkeypatch.setattr(BUILDER, "source_paths", lambda root: [root / "input.txt"])
    monkeypatch.setattr(BUILDER.shutil, "which", lambda tool: f"/tools/{tool}")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        output = Path(command[command.index("--output") + 1])
        output.mkdir()
        (output / "result.whl").write_text("fixture")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(BUILDER, "run", run)

    BUILDER.main([
        "--targets", "windows-x86_64", "--cross-compile-windows",
        "--output", str(tmp_path / "results"),
    ])

    command = calls[0]
    assert command[-1] == "--cross-compile-windows"
    assert command[command.index("--stage") + 1] == "build"
    assert (tmp_path / "results/windows-x86_64/result.whl").read_text() == "fixture"


def test_staging_excludes_secrets_git_and_build_outputs():
    paths = BUILDER.source_paths(ROOT)
    relative = {path.relative_to(ROOT).as_posix() for path in paths}
    assert "tools/build_wheels.py" in relative
    assert "tools/bids-validator-rust/Cargo.lock" in relative
    assert "src/npdb/external/validator-source.json" in relative
    assert not any(
        name.startswith((".venv/", "seed-datasets/", "build/", ".git/"))
        or name.endswith((".pyc", "/.git"))
        or name == ".env"
        for name in relative
    )


def test_linux_prepares_then_verifies_in_separate_offline_container(monkeypatch, tmp_path):
    calls = Mock()
    monkeypatch.setattr(BUILDER, "container_run", calls)
    BUILDER.build_linux("linux-arm64", tmp_path / "project", tmp_path / "output", True)
    build, verify = calls.call_args_list
    assert build.args[:2] == ("quay.io/pypa/manylinux_2_28_aarch64", "linux/arm64")
    assert "--verify" in build.args[2]
    assert verify.kwargs == {"offline": True}
    assert verify.args[2][-2:] == ["--stage", "verify"]


@pytest.mark.parametrize("failure", ["copy", "exit"])
def test_container_cleanup_and_error_propagation(monkeypatch, tmp_path, failure):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "create":
            return subprocess.CompletedProcess(command, 0, stdout="container-id\n")
        if command[1] == "cp" and failure == "copy":
            raise subprocess.CalledProcessError(1, command)
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, stdout="17\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(BUILDER, "run", run)
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        BUILDER.container_run("image", "linux/amd64", ["build"], tmp_path, tmp_path)
    assert commands[-1] == ["docker", "rm", "--force", "container-id"]
    assert not any(command[:3] == ["docker", "cp", "container-id:/output/."] for command in commands)


def test_multiple_targets_use_fresh_staging_and_separate_outputs(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("source")
    monkeypatch.setattr(BUILDER, "ROOT", source)
    monkeypatch.setattr(BUILDER, "source_paths", lambda root: [root / "input.txt"])
    monkeypatch.setattr(BUILDER.shutil, "which", lambda tool: tool)
    monkeypatch.setattr(BUILDER, "run", Mock())
    built = []

    def build(target, project, output, verify):
        assert (project / "input.txt").read_text() == "source"
        assert not (project / "build-output").exists()
        (project / "build-output").touch()
        assert not output.exists()
        assert verify
        built.append(target)
        output.mkdir()
        (output / "result.whl").write_text(target)

    monkeypatch.setattr(BUILDER, "build_linux", build)
    output = tmp_path / "results"
    BUILDER.main([
        "--targets", "linux-x86_64", "linux-arm64", "linux-x86_64",
        "--verify", "--output", str(output),
    ])
    assert built == ["linux-x86_64", "linux-arm64"]
    for target in built:
        assert (output / target / "result.whl").read_text() == target


def test_captured_docker_errors_are_visible(capsys, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="Registry unavailable")

    monkeypatch.setattr(BUILDER.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        BUILDER.run(["docker", "create", "image"], capture_output=True, text=True)
    assert "Registry unavailable" in capsys.readouterr().err


def test_native_build_uses_target_environment_and_optional_wheelhouse(monkeypatch, tmp_path):
    calls = Mock()
    monkeypatch.setattr(BUILDER, "run", calls)
    (tmp_path / "example.whl").touch()
    BUILDER.build("macos-arm64", tmp_path, True)
    sdist, wheel, wheelhouse = calls.call_args_list
    assert sdist.args[0][:3] == ["uv", "build", "--sdist"]
    assert wheel.args[0][:3] == ["uv", "build", "--wheel"]
    assert wheel.kwargs["env"]["NPDB_RUST_TARGET"] == "aarch64-apple-darwin"
    assert wheel.kwargs["env"]["NPDB_WHEEL_PLATFORM"] == "macosx_11_0_arm64"
    assert wheel.kwargs["env"]["MACOSX_DEPLOYMENT_TARGET"] == "11.0"
    assert wheelhouse.args[0][1:4] == ["-m", "pip", "wheel"]


def test_cross_build_sets_xwin_environment_without_preparing_wheelhouse(
    monkeypatch, tmp_path,
):
    calls = Mock()
    monkeypatch.setattr(BUILDER, "run", calls)
    BUILDER.build(
        "windows-x86_64", tmp_path, False, cross_compile_windows=True,
    )
    assert calls.call_count == 2
    assert calls.call_args.kwargs["env"]["NPDB_CARGO_XWIN"] == "1"


def test_cross_build_sets_xwin_environment_without_preparing_wheelhouse(monkeypatch, tmp_path):
    calls = Mock()
    monkeypatch.setattr(BUILDER, "run", calls)
    BUILDER.build(
        "windows-x86_64", tmp_path, False, cross_compile_windows=True,
    )
    assert calls.call_count == 2
    assert calls.call_args.kwargs["env"]["NPDB_CARGO_XWIN"] == "1"


def test_offline_container_has_no_network_or_bind_mounts(monkeypatch, tmp_path):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        output = "container-id\n" if command[1] == "create" else "0\n"
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(BUILDER, "run", run)
    BUILDER.container_run(
        "image", "linux/amd64", ["verify"], tmp_path, tmp_path, offline=True,
    )
    assert commands[0] == [
        "docker", "create", "--platform", "linux/amd64", "--workdir", "/project",
        "--network", "none", "image", "verify",
    ]
    assert commands[-2] == [
        "docker", "cp", "container-id:/output/validator-sizes.json",
        str(tmp_path / "validator-sizes.json"),
    ]
    assert commands[-1] == ["docker", "rm", "--force", "container-id"]


def test_build_without_verification_does_not_prepare_dependencies(monkeypatch, tmp_path):
    calls = Mock()
    monkeypatch.setattr(BUILDER, "run", calls)
    BUILDER.build("windows-x86_64", tmp_path, False)
    assert calls.call_count == 2
    assert calls.call_args.kwargs["env"]["NPDB_WHEEL_PLATFORM"] == "win_amd64"


@pytest.mark.parametrize("target", ["windows-x86_64", "macos-arm64"])
def test_foreign_artifact_build_does_not_require_rust(monkeypatch, tmp_path, target):
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(BUILDER, "ROOT", source)
    monkeypatch.setattr(BUILDER, "host_target", lambda: "linux-x86_64")
    monkeypatch.setattr(BUILDER, "source_paths", lambda root: [])
    tools = Mock(side_effect=lambda name: "/tools/uv" if name == "uv" else None)
    monkeypatch.setattr(BUILDER.shutil, "which", tools)

    def run(command, **kwargs):
        output = Path(command[command.index("--output") + 1])
        output.mkdir()
        (output / "result.whl").write_text("fixture")
        assert "NPDB_VALIDATOR_CACHE" in kwargs["env"]

    monkeypatch.setattr(BUILDER, "run", run)
    BUILDER.main(["--targets", target, "--output", str(tmp_path / "results")])
    assert [call.args[0] for call in tools.call_args_list] == ["uv"]
    assert (tmp_path / "results" / target / "result.whl").is_file()


def test_source_only_build_sets_override(monkeypatch, tmp_path):
    calls = Mock()
    monkeypatch.setattr(BUILDER, "run", calls)
    BUILDER.build("macos-arm64", tmp_path, False, source_only=True)
    assert calls.call_args.kwargs["env"]["NPDB_VALIDATOR_SOURCE_ONLY"] == "1"


def test_source_only_is_forwarded_to_linux_container(monkeypatch, tmp_path):
    calls = Mock()
    monkeypatch.setattr(BUILDER, "container_run", calls)
    BUILDER.build_linux(
        "linux-x86_64", tmp_path / "project", tmp_path / "output", False, source_only=True,
    )
    assert "--source-only" in calls.call_args.args[2]

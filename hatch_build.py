"""Attach one native validator to each non-editable wheel."""

import os
import platform
import runpy
import sysconfig
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        if self.target_name == "sdist":
            runpy.run_path(str(root / "tools/build_validator.py"))["source_directory"](
                root
            )
            return
        if self.target_name != "wheel" or version == "editable":
            return
        target = os.environ.get("NPDB_RUST_TARGET")
        stage = root / "build" / "validator" / (target or sysconfig.get_platform())
        build = runpy.run_path(str(root / "tools/build_validator.py"))["build_bundle"]
        build(root, stage, target)
        wheel_platform = os.environ.get(
            "NPDB_WHEEL_PLATFORM",
            sysconfig.get_platform().replace("-", "_").replace(".", "_"),
        )
        architecture = platform.machine().lower()
        expected_arch = {
            "x86_64": ("x86_64", "amd64"),
            "amd64": ("x86_64", "amd64"),
            "aarch64": ("aarch64", "arm64"),
            "arm64": ("aarch64", "arm64"),
        }.get(architecture)
        if expected_arch is None or not wheel_platform.endswith(expected_arch):
            raise RuntimeError(
                f"Wheel platform {wheel_platform} does not match host {architecture}"
            )
        if wheel_platform.startswith(("manylinux_", "musllinux_", "macosx_", "win_")):
            name = (
                "bids-validator.exe"
                if wheel_platform.startswith("win_")
                else "bids-validator"
            )
            runpy.run_path(str(root / "tools/audit_validator.py"))["audit"](
                stage / name, wheel_platform
            )
        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{wheel_platform}"
        build_data["force_include"][str(stage)] = "bids_repair/_bin"

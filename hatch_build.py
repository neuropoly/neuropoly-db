"""Package a native validator without ever changing its upstream submodule."""

import json
import os
import runpy
import sysconfig
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        try:
            self._initialize(version, build_data)
        except (RuntimeError, OSError) as error:
            if self.app.verbosity > 0:
                raise

            self.app.abort(f"error: {error}")

    def _initialize(self, version, build_data):
        root = Path(self.root)
        helpers = runpy.run_path(str(root / "tools/build_validator.py"))

        if (
            (root / "build")
            .resolve()
            .is_relative_to((root / "tools/bids-validator-rust").resolve())
        ):
            raise RuntimeError("Build staging must be outside the validator submodule.")

        if self.target_name == "sdist":
            helpers["source_directory"](root)
            files = helpers["source_files"](root)

            manifest = root / "build/validator-source-files.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(
                json.dumps(helpers["source_manifest"](root), indent=2) + "\n",
                encoding="utf-8",
            )

            for path in files:
                build_data["force_include"][str(path)] = path.relative_to(
                    root
                ).as_posix()

            build_data["force_include"][
                str(manifest)
            ] = "tools/validator-source-files.json"
            return

        if self.target_name != "wheel" or version == "editable":
            return

        target = os.environ.get("NPDB_RUST_TARGET")
        wheel_platform = os.environ.get(
            "NPDB_WHEEL_PLATFORM",
            sysconfig.get_platform().replace("-", "_").replace(".", "_"),
        )
        stage = root / "build/validator" / wheel_platform
        executable = helpers["build_bundle"](root, stage, target)
        provenance = json.loads((stage / "provenance.json").read_text())
        actual_target = provenance["target"]

        if wheel_platform.startswith(("linux_", "manylinux_")):
            wheel_platform = provenance["wheel_platform"]

        architectures = {
            "x86_64": ("x86_64", "amd64"),
            "aarch64": ("aarch64", "arm64"),
        }
        architecture = actual_target.split("-")[0]

        if architecture not in architectures or not wheel_platform.endswith(
            architectures[architecture]
        ):
            raise RuntimeError("Native architecture does not match the wheel platform.")

        system = (
            "windows"
            if wheel_platform.startswith("win_")
            else ("darwin" if wheel_platform.startswith("macosx_") else "linux")
        )

        if system not in actual_target:
            raise RuntimeError(
                "Native operating system does not match the wheel platform."
            )

        if not wheel_platform.startswith("linux_"):
            runpy.run_path(str(root / "tools/audit_validator.py"))["audit"](
                executable, wheel_platform
            )
        elif wheel_platform != sysconfig.get_platform().replace("-", "_").replace(
            ".", "_"
        ):
            raise RuntimeError(
                "A local Linux wheel must match the build host platform."
            )

        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{wheel_platform}"
        build_data["force_include"][str(stage)] = "npdb/_bin"

"""Bundle verified Microsoft Visual C++ redistributables, never PATH DLLs."""

import hashlib
import io
import json
import re
import runpy
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

# Microsoft Visual Studio 17 release manifest, CRT.Redist.X64.base 14.44.35211.
RUNTIME_SHA256 = "4aaf54db0bfc9435f7c3660e1a00237a4b556042bfeea64bde44c2e0194e6ee5"
RUNTIME_URL = (
    "https://download.visualstudio.microsoft.com/download/pr/"
    "45d3b8dd-bced-4b37-9974-142f748d710c/"
    + RUNTIME_SHA256
    + "/Microsoft.VC.14.44.17.14.CRT.Redist.X64.base.vsix"
)
RUNTIME_PREFIX = "Contents/VC/Redist/MSVC/14.44.35112/x64/Microsoft.VC143.CRT/"
LICENSE_URL = (
    "https://visualstudio.microsoft.com/wp-content/uploads/2021/09/"
    "Visual-C-Runtime-2015-2022-License-1.docx"
)
LICENSE_SHA256 = "f1e3d56ceb2ad68aae0711b910375009e651ac5530fa0760f0dea6e81e54fae1"


def download_runtime(destination: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    common = runpy.run_path(str(Path(__file__).with_name("validator_acquisition.py")))
    print("Preparing checksum-pinned Microsoft x64 runtime 14.44.35211...", flush=True)

    data = common["cached"](root, RUNTIME_URL, RUNTIME_SHA256, "windows-runtime")
    license_data = common["cached"](
        root, LICENSE_URL, LICENSE_SHA256, "windows-runtime"
    )
    files = {}

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 256
                or sum(entry.file_size for entry in entries) > 32 * 1024 * 1024
            ):
                raise RuntimeError("Microsoft runtime archive exceeds the size limit.")

            for entry in entries:
                if not entry.filename.startswith(RUNTIME_PREFIX):
                    continue

                name = entry.filename.removeprefix(RUNTIME_PREFIX).lower()
                if not re.fullmatch(r"[a-z0-9_]+\.dll", name) or name in files:
                    raise RuntimeError(
                        f"Invalid or duplicate Microsoft runtime DLL: {name}"
                    )

                binary = archive.read(entry)
                (destination / name).write_bytes(binary)
                files[name] = hashlib.sha256(binary).hexdigest()

        if "vcruntime140.dll" not in files:
            raise RuntimeError("Microsoft runtime package lacks vcruntime140.dll.")

        with zipfile.ZipFile(io.BytesIO(license_data)) as archive:
            document = archive.getinfo("word/document.xml")
            if document.file_size > 2 * 1024 * 1024:
                raise RuntimeError("Microsoft runtime license exceeds the size limit.")

            tree = ET.fromstring(archive.read(document))
            namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            license_text = "\n".join(
                "".join(node.text or "" for node in paragraph.iter(namespace + "t"))
                for paragraph in tree.iter(namespace + "p")
            )

        if "MICROSOFT" not in license_text or "LICENSE" not in license_text:
            raise RuntimeError(
                "Microsoft runtime license document has no license text."
            )

    except (ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as error:
        raise RuntimeError(
            f"Invalid Microsoft runtime package or license: {error}"
        ) from error

    license_name = "MICROSOFT-RUNTIME-TERMS.txt"
    license_file = destination / license_name
    license_file.write_text(license_text + "\n", encoding="utf-8")

    (destination / "redistribution.json").write_text(
        json.dumps(
            {
                "authorized": True,
                "origin": RUNTIME_URL,
                "version": "14.44.35211",
                "archive_sha256": RUNTIME_SHA256,
                "license_url": LICENSE_URL,
                "license_archive_sha256": LICENSE_SHA256,
                "license": license_name,
                "license_sha256": hashlib.sha256(license_file.read_bytes()).hexdigest(),
                "files": files,
            }
        ),
        encoding="utf-8",
    )

    return destination


def bundle(executable: Path, runtime: str) -> None:
    audit = runpy.run_path(str(Path(__file__).with_name("audit_validator.py")))

    if all(audit["system_dll"](name) for name in audit["pe_imports"](executable)):
        return

    if runtime:
        _bundle(executable, runtime)
        return

    with tempfile.TemporaryDirectory(prefix="npdb-vc-redist-") as temporary:
        try:
            directory = download_runtime(Path(temporary))
            _bundle(executable, str(directory))
        except (RuntimeError, OSError) as error:
            raise RuntimeError(
                f"Cannot prepare the Windows runtime: {error}\n"
                "Check network/cache access, or use --windows-runtime-dir with verified "
                "x64 redistributables and redistribution.json."
            ) from error


def _bundle(executable: Path, runtime: str) -> None:
    audit = runpy.run_path(str(Path(__file__).with_name("audit_validator.py")))
    pending = [executable]
    required = set()
    manifest = None
    directory = Path(runtime) if runtime else None

    while pending:
        image = pending.pop()

        for dll in audit["pe_imports"](image):
            if audit["system_dll"](dll) or dll in required:
                continue

            if directory is None:
                raise RuntimeError(
                    f"{dll} must be bundled. Set --windows-runtime-dir to authorized "
                    "x64 Visual Studio redist files with redistribution.json."
                )

            if manifest is None:
                manifest_file = directory / "redistribution.json"

                try:
                    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                except (OSError, ValueError) as error:
                    raise RuntimeError(
                        f"Cannot read Windows runtime manifest {manifest_file}: {error}"
                    ) from error

                if not isinstance(manifest, dict):
                    raise RuntimeError(
                        "Runtime redistribution manifest must be a JSON object."
                    )

                host = urlsplit(manifest.get("origin", "")).hostname or ""
                if (
                    manifest.get("authorized") is not True
                    or not (
                        host.endswith(".microsoft.com")
                        or host == "visualstudio.microsoft.com"
                    )
                    or not isinstance(manifest.get("version"), str)
                    or not isinstance(manifest.get("files"), dict)
                ):
                    raise RuntimeError(
                        "Runtime redistribution manifest lacks authorized Microsoft provenance."
                    )

            source = directory / dll
            if (
                source.is_symlink()
                or not source.is_file()
                or hashlib.sha256(source.read_bytes()).hexdigest()
                != manifest["files"].get(dll)
            ):
                raise RuntimeError(f"Missing/unverified authorized runtime DLL: {dll}")

            audit["pe_imports"](source)
            target = executable.parent / dll
            shutil.copy2(source, target)
            required.add(dll)
            pending.append(target)

    if manifest is not None and directory is not None:
        license_name = manifest.get("license")
        if not isinstance(license_name, str) or Path(license_name).name != license_name:
            raise RuntimeError(
                "Runtime manifest must identify a local redistribution license file."
            )

        license_file = directory / license_name
        if (
            license_file.is_symlink()
            or not license_file.is_file()
            or hashlib.sha256(license_file.read_bytes()).hexdigest()
            != manifest.get("license_sha256")
        ):
            raise RuntimeError("Missing or unverified runtime redistribution notices.")

        shutil.copy2(license_file, executable.parent / "WINDOWS-RUNTIME-LICENSE")
        (executable.parent / "windows-runtime.json").write_text(
            json.dumps(
                {
                    **manifest,
                    "license": "WINDOWS-RUNTIME-LICENSE",
                    "files": {
                        name: manifest["files"][name] for name in sorted(required)
                    },
                },
                indent=2,
            )
            + "\n"
        )

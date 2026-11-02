"""Check native architecture and runtime requirements before portable tagging."""

import re
import struct
import subprocess
from pathlib import Path


def _output(*command: str) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def audit(binary: Path, wheel_platform: str) -> None:
    if wheel_platform.startswith(("manylinux_", "musllinux_")):
        if wheel_platform not in {
            "manylinux_2_28_x86_64", "manylinux_2_28_aarch64",
            "musllinux_1_2_x86_64", "musllinux_1_2_aarch64",
        }:
            raise ValueError(f"Unsupported Linux baseline: {wheel_platform}")
        machine = "AArch64" if wheel_platform.endswith("aarch64") else "Advanced Micro Devices X86-64"
        if machine not in _output("readelf", "-h", str(binary)):
            raise RuntimeError("ELF architecture does not match the wheel tag.")
        dynamic = _output("readelf", "-d", str(binary))
        versions = _output("readelf", "--version-info", str(binary))
        libraries = set(re.findall(r"Shared library: \[(.*?)\]", dynamic))
        if wheel_platform.startswith("manylinux_"):
            allowed = {
                "libc.so.6", "libdl.so.2", "libgcc_s.so.1", "libm.so.6",
                "libpthread.so.0", "ld-linux-x86-64.so.2", "ld-linux-aarch64.so.1",
            }
            required = [tuple(map(int, value.split(".")))
                        for value in re.findall(r"GLIBC_([0-9.]+)", versions)]
            if required and max(required) > (2, 28):
                raise RuntimeError(f"Executable requires glibc {max(required)}, above 2.28.")
        else:
            allowed = {"libc.musl-x86_64.so.1", "libc.musl-aarch64.so.1"}
            if "GLIBC_" in versions:
                raise RuntimeError("A musllinux executable cannot require glibc.")
        if libraries - allowed:
            raise RuntimeError(f"Unbundled native dependencies: {libraries - allowed}")
    elif wheel_platform in {"macosx_11_0_x86_64", "macosx_11_0_arm64"}:
        expected = "arm64" if wheel_platform.endswith("arm64") else "x86_64"
        if _output("lipo", "-archs", str(binary)).strip() != expected:
            raise RuntimeError("Mach-O architecture does not match the wheel tag.")
        versions = _output("vtool", "-show-build", str(binary))
        minimum = re.findall(r"\bminos\s+([0-9.]+)", versions)
        if not minimum or any(
            tuple((list(map(int, value.split("."))) + [0, 0])[:3]) > (11, 0, 0)
            for value in minimum
        ):
            raise RuntimeError(f"Executable exceeds macOS 11.0 baseline: {versions}")
        libraries = _output("otool", "-L", str(binary)).splitlines()[1:]
        if any(not line.strip().startswith(("/usr/lib/", "/System/Library/"))
               for line in libraries):
            raise RuntimeError(f"Unbundled native dependencies: {libraries}")
    elif wheel_platform == "win_amd64":
        data = binary.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise RuntimeError("Invalid PE executable.")
        machine, sections = struct.unpack_from("<HH", data, pe + 4)
        if machine != 0x8664:
            raise RuntimeError("PE architecture does not match win_amd64.")
        optional_size = struct.unpack_from("<H", data, pe + 20)[0]
        optional = pe + 24
        if struct.unpack_from("<H", data, optional)[0] != 0x20B:
            raise RuntimeError("Expected PE32+ executable.")
        import_rva = struct.unpack_from("<I", data, optional + 120)[0]

        def offset(rva: int) -> int:
            for index in range(sections):
                section = optional + optional_size + index * 40
                size, address, raw_size, raw = struct.unpack_from("<IIII", data, section + 8)
                if address <= rva < address + max(size, raw_size):
                    return raw + rva - address
            raise RuntimeError(f"Unmapped PE import RVA: {rva}")

        if import_rva:
            descriptor = offset(import_rva)
            while any(data[descriptor:descriptor + 20]):
                name = offset(struct.unpack_from("<I", data, descriptor + 12)[0])
                dll = data[name:data.index(b"\0", name)].decode("ascii").lower()
                if dll not in {
                    "kernel32.dll", "ntdll.dll", "bcrypt.dll", "bcryptprimitives.dll",
                    "advapi32.dll", "userenv.dll", "ws2_32.dll", "synchronization.dll",
                } and not dll.startswith(("api-ms-win-", "ext-ms-win-")):
                    raise RuntimeError(f"Unbundled Windows runtime dependency: {dll}")
                descriptor += 20
    elif wheel_platform.startswith(("linux_", "macosx_")):
        # Local wheels use the build interpreter's exact platform, not a portable claim.
        import sysconfig

        local = sysconfig.get_platform().replace("-", "_").replace(".", "_")
        if wheel_platform != local:
            raise ValueError(f"Unsupported portable platform: {wheel_platform}")
    else:
        raise ValueError(f"Unsupported wheel platform: {wheel_platform}")

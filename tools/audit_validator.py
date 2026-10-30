"""Reject incompatible native dependencies before assigning a portable wheel tag."""

import argparse
import re
import struct
import subprocess
from pathlib import Path


def audit(binary: Path, wheel_platform: str) -> None:
    if wheel_platform.startswith(("manylinux_", "musllinux_")):
        header = subprocess.run(
            ["readelf", "-h", str(binary)], check=True, capture_output=True, text=True
        ).stdout
        machine = (
            "AArch64"
            if wheel_platform.endswith("aarch64")
            else "Advanced Micro Devices X86-64"
        )
        if machine not in header:
            raise RuntimeError("ELF architecture does not match the wheel tag.")
        dynamic = subprocess.run(
            ["readelf", "-d", str(binary)], check=True, capture_output=True, text=True
        ).stdout
        versions = subprocess.run(
            ["readelf", "--version-info", str(binary)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        libraries = re.findall(r"Shared library: \[(.*?)\]", dynamic)
        if wheel_platform.startswith("manylinux_"):
            allowed = {
                "libc.so.6",
                "libdl.so.2",
                "libgcc_s.so.1",
                "libm.so.6",
                "libpthread.so.0",
                "ld-linux-x86-64.so.2",
                "ld-linux-aarch64.so.1",
            }
            required = [
                tuple(map(int, value.split(".")))
                for value in re.findall(r"GLIBC_([0-9.]+)", versions)
            ]
            if required and max(required) > (2, 28):
                raise RuntimeError(
                    f"Executable requires glibc {max(required)}, above 2.28."
                )
        else:
            allowed = {"libc.musl-x86_64.so.1", "libc.musl-aarch64.so.1"}
        if set(libraries) - allowed:
            raise RuntimeError(
                f"Unbundled native dependencies: {set(libraries) - allowed}"
            )
    elif wheel_platform.startswith("macosx_"):
        architecture = subprocess.run(
            ["lipo", "-archs", str(binary)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        expected = "arm64" if wheel_platform.endswith("arm64") else "x86_64"
        if architecture != expected:
            raise RuntimeError("Mach-O architecture does not match the wheel tag.")
        versions = subprocess.run(
            ["vtool", "-show-build", str(binary)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        minimum = re.findall(r"\bminos\s+([0-9.]+)", versions)
        if not minimum or any(
            tuple(map(int, value.split("."))) > (11, 0, 0) for value in minimum
        ):
            raise RuntimeError(
                f"Executable exceeds the macOS 11.0 baseline: {versions}"
            )
        libraries = subprocess.run(
            ["otool", "-L", str(binary)], check=True, capture_output=True, text=True
        ).stdout.splitlines()[1:]
        if any(
            not line.strip().startswith(("/usr/lib/", "/System/Library/"))
            for line in libraries
        ):
            raise RuntimeError(f"Unbundled native dependencies: {libraries}")
    elif wheel_platform.startswith("win_"):
        data = binary.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe : pe + 4] != b"PE\0\0":
            raise RuntimeError("Invalid PE executable.")
        machine, sections = struct.unpack_from("<HH", data, pe + 4)
        if machine != {"win_amd64": 0x8664, "win_arm64": 0xAA64}[wheel_platform]:
            raise RuntimeError(
                "Windows executable architecture does not match wheel tag."
            )
        optional_size = struct.unpack_from("<H", data, pe + 20)[0]
        optional = pe + 24
        if struct.unpack_from("<H", data, optional)[0] != 0x20B:
            raise RuntimeError("Expected PE32+ executable.")
        import_rva = struct.unpack_from("<I", data, optional + 120)[0]

        def offset(rva):
            for index in range(sections):
                section = optional + optional_size + index * 40
                size, address, raw_size, raw = struct.unpack_from(
                    "<IIII", data, section + 8
                )
                if address <= rva < address + max(size, raw_size):
                    return raw + rva - address
            raise RuntimeError(f"Unmapped PE import RVA: {rva}")

        if import_rva:
            descriptor = offset(import_rva)
            while any(data[descriptor : descriptor + 20]):
                name = offset(struct.unpack_from("<I", data, descriptor + 12)[0])
                dll = data[name : data.index(b"\0", name)].decode("ascii").lower()
                if dll not in {
                    "kernel32.dll",
                    "ntdll.dll",
                    "bcrypt.dll",
                    "bcryptprimitives.dll",
                    "advapi32.dll",
                    "userenv.dll",
                    "ws2_32.dll",
                    "synchronization.dll",
                } and not dll.startswith(("api-ms-win-", "ext-ms-win-")):
                    raise RuntimeError(f"Unbundled Windows runtime dependency: {dll}")
                descriptor += 20
    else:
        raise ValueError(f"No portable compatibility policy for {wheel_platform}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("wheel_platform")
    args = parser.parse_args()
    audit(args.binary, args.wheel_platform)

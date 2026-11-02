"""Check native architecture and runtime requirements before portable tagging."""

import re
import struct
import subprocess
from pathlib import Path


def _output(*command: str) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def audit_macho(data: bytes, expected: int) -> None:
    """Check thin or universal Mach-O executables on any build host."""
    try:
        if data[:4] == b"\xca\xfe\xba\xbe":
            count, = struct.unpack_from(">I", data, 4)
            if count not in (1, 2):
                raise RuntimeError("Expected one or two Mach-O architectures.")
            slices = []
            for index in range(count):
                cpu, _, offset, size, _ = struct.unpack_from(">IIIII", data, 8 + index * 20)
                if offset < 8 + count * 20 or offset + size > len(data):
                    raise RuntimeError("Invalid Mach-O slice bounds.")
                slices.append((cpu, data[offset:offset + size]))
        else:
            cpu, = struct.unpack_from("<I", data, 4)
            slices = [(cpu, data)]
        architectures = [cpu for cpu, _ in slices]
        if (
            expected not in architectures or len(set(architectures)) != len(architectures)
            or not set(architectures) <= {0x01000007, 0x0100000C}
        ):
            raise RuntimeError("Mach-O architecture does not match the wheel tag.")
        for cpu, image in slices:
            if image[:4] != b"\xcf\xfa\xed\xfe":
                raise RuntimeError("Expected a little-endian 64-bit Mach-O executable.")
            actual_cpu, _, kind, count, size = struct.unpack_from("<IIIII", image, 4)
            if actual_cpu != cpu or kind != 2 or 32 + size > len(image):
                raise RuntimeError("Invalid Mach-O executable header.")
            position = 32
            minimums = []
            for _ in range(count):
                command, length = struct.unpack_from("<II", image, position)
                if length < 8 or position + length > 32 + size:
                    raise RuntimeError("Invalid Mach-O load command.")
                payload = image[position:position + length]
                if command == 0x24:
                    minimum, = struct.unpack_from("<I", payload, 8)
                    minimums.append(minimum)
                elif command == 0x32:
                    system, minimum = struct.unpack_from("<II", payload, 8)
                    minimums.append(minimum)
                    if system != 1:
                        raise RuntimeError("Mach-O binary does not target macOS.")
                elif command in {0xC, 0x80000018, 0x8000001F, 0x20, 0x80000023}:
                    start, = struct.unpack_from("<I", payload, 8)
                    if not 24 <= start < length or b"\0" not in payload[start:]:
                        raise RuntimeError("Invalid Mach-O library load command.")
                    library = payload[start:].split(b"\0", 1)[0].decode("utf-8")
                    if not library.startswith(("/usr/lib/", "/System/Library/")):
                        raise RuntimeError(f"Unbundled native dependency: {library}")
                position += length
            if (
                position != 32 + size or not minimums
                or any(minimum > 11 << 16 for minimum in minimums)
            ):
                raise RuntimeError("Executable exceeds macOS 11.0 baseline or has no deployment target.")
    except (struct.error, UnicodeError) as error:
        raise RuntimeError(f"Invalid Mach-O executable: {error}") from error


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
        expected = 0x0100000C if wheel_platform.endswith("arm64") else 0x01000007
        audit_macho(binary.read_bytes(), expected)
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

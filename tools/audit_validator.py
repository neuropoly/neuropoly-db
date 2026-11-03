"""Check native architecture and runtime requirements before portable tagging."""

import re
import struct
import subprocess
from pathlib import Path


def _output(*command: str) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def linux_baseline(binary: Path) -> tuple[int, int]:
    versions = _output("readelf", "--version-info", str(binary))
    if re.search(r"GLIBC_[A-Z]", versions):
        raise RuntimeError("Unsupported nonnumeric glibc ABI requirement.")
    required = [tuple(map(int, value.split(".")))
                for value in re.findall(r"GLIBC_([0-9.]+)", versions)]
    return max([(2, 17), *required])


def pe_imports(binary: Path) -> set[str]:
    """Read x64 PE imports without executing the image or requiring Windows."""
    data = binary.read_bytes()
    try:
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[:2] != b"MZ" or data[pe:pe + 4] != b"PE\0\0":
            raise RuntimeError("Invalid PE executable.")
        machine, sections = struct.unpack_from("<HH", data, pe + 4)
        if machine != 0x8664 or not 1 <= sections <= 96:
            raise RuntimeError("PE architecture does not match win_amd64.")
        optional_size = struct.unpack_from("<H", data, pe + 20)[0]
        optional = pe + 24
        if struct.unpack_from("<H", data, optional)[0] != 0x20B:
            raise RuntimeError("Expected PE32+ executable.")

        def offset(rva: int) -> int:
            for index in range(sections):
                section = optional + optional_size + index * 40
                size, address, raw_size, raw = struct.unpack_from("<IIII", data, section + 8)
                if address <= rva < address + min(size, raw_size):
                    result = raw + rva - address
                    if result < len(data):
                        return result
            raise RuntimeError(f"Unmapped PE import RVA: {rva}")

        imports = set()
        # Normal and delay-load dependencies both participate in the closure.
        for directory, stride, name_offset in ((120, 20, 12), (216, 32, 4)):
            rva = struct.unpack_from("<I", data, optional + directory)[0]
            if not rva:
                continue
            descriptor = offset(rva)
            for _ in range(1024):
                if descriptor + stride > len(data):
                    raise RuntimeError("Truncated PE import table.")
                if not any(data[descriptor:descriptor + stride]):
                    break
                if stride == 32 and struct.unpack_from("<I", data, descriptor)[0] != 1:
                    raise RuntimeError("Unsupported PE delay import addressing.")
                name = offset(struct.unpack_from("<I", data, descriptor + name_offset)[0])
                end = data.index(b"\0", name, min(name + 256, len(data)))
                dll = data[name:end].decode("ascii").lower()
                if not re.fullmatch(r"[a-z0-9_.-]+\.dll", dll):
                    raise RuntimeError("Unsafe PE DLL name.")
                imports.add(dll)
                descriptor += stride
            else:
                raise RuntimeError("PE import table exceeds size limit.")
        return imports
    except (struct.error, ValueError, UnicodeError) as error:
        raise RuntimeError(f"Invalid PE image {binary}: {error}") from error


def system_dll(name: str) -> bool:
    return name in {
        "kernel32.dll", "ntdll.dll", "bcrypt.dll", "bcryptprimitives.dll",
        "advapi32.dll", "userenv.dll", "ws2_32.dll", "synchronization.dll",
    } or name.startswith(("api-ms-win-", "ext-ms-win-"))


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
            "musllinux_1_2_x86_64", "musllinux_1_2_aarch64",
        } and not re.fullmatch(r"manylinux_2_[0-9]+_(x86_64|aarch64)", wheel_platform):
            raise ValueError(f"Unsupported Linux baseline: {wheel_platform}")
        machine = "AArch64" if wheel_platform.endswith("aarch64") else "Advanced Micro Devices X86-64"
        if machine not in _output("readelf", "-h", str(binary)):
            raise RuntimeError("ELF architecture does not match the wheel tag.")
        dynamic = _output("readelf", "-d", str(binary))
        versions = _output("readelf", "--version-info", str(binary))
        if re.search(r"GLIBC_[A-Z]", versions):
            raise RuntimeError("Unsupported nonnumeric glibc ABI requirement.")
        libraries = set(re.findall(r"Shared library: \[(.*?)\]", dynamic))
        if wheel_platform.startswith("manylinux_"):
            allowed = {
                "libc.so.6", "libdl.so.2", "libgcc_s.so.1", "libm.so.6",
                "libpthread.so.0", "ld-linux-x86-64.so.2", "ld-linux-aarch64.so.1",
            }
            required = [tuple(map(int, value.split(".")))
                        for value in re.findall(r"GLIBC_([0-9.]+)", versions)]
            baseline = tuple(map(int, wheel_platform.split("_")[1:3]))
            if required and max(required) > baseline:
                raise RuntimeError(f"Executable requires glibc {max(required)}, above {baseline}.")
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
        pending = [binary]
        visited = set()
        while pending:
            image = pending.pop()
            if image.name.lower() in visited:
                continue
            visited.add(image.name.lower())
            for dll in pe_imports(image):
                if system_dll(dll):
                    continue
                dependency = binary.parent / dll
                if not dependency.is_file():
                    raise RuntimeError(f"Unbundled Windows runtime dependency: {dll}")
                pending.append(dependency)
    elif wheel_platform.startswith(("linux_", "macosx_")):
        # Local wheels use the build interpreter's exact platform, not a portable claim.
        import sysconfig

        local = sysconfig.get_platform().replace("-", "_").replace(".", "_")
        if wheel_platform != local:
            raise ValueError(f"Unsupported portable platform: {wheel_platform}")
    else:
        raise ValueError(f"Unsupported wheel platform: {wheel_platform}")

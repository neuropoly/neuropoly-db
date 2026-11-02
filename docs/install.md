# Install npdb

Use a matching prebuilt wheel to install npdb with its pinned native Rust BIDS
validator, without installing Rust yourself. Source installations require an
initialized validator submodule and use Rust if no approved artifact is usable.

## 1. Install the tools npdb relies on

`npdb` downloads with **git** and **git-annex**.

- **Ubuntu/Debian:**

  ```bash
  sudo apt-get install -y git git-annex
  ```

- **macOS** (with [Homebrew](https://brew.sh)):

  ```bash
  brew install git git-annex
  ```

## 2. Install uv

[uv](https://docs.astral.sh/uv/) installs `npdb` and the right Python for you.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Close and reopen your terminal** afterwards.

## 3. Install npdb

Download the wheel for your platform from the **Native validator wheels**
GitHub Actions artifacts when available, extract the artifact, then install
the wheel file (replace the example filename with the downloaded filename):

```bash
uv tool install ./npdb-0.1.0-py3-none-manylinux_2_28_x86_64.whl
```

The wheel build matrix covers Linux glibc 2.28+ on x86-64/ARM64,
macOS Intel/Apple Silicon, and Windows x86-64: five variants. Native macOS
validator binaries target macOS 11+, but Python dependencies may require a newer
OS. Alpine/musl is excluded because the required Playwright dependency has no
musllinux distribution. Windows ARM64 is not supported by the complete
dependency wheelhouse.
On Windows, the wider git-annex/data workflow can also be run in
[WSL](https://learn.microsoft.com/windows/wsl/install).

Wheels contain the validator executable, embedded-schema identity, provenance
and third-party notices. Python dependencies are still downloaded during a
normal install; a wheel is not a self-contained offline installer for all npdb
dependencies. CI verifies disconnected installation from a complete prepared
wheelhouse, including dependencies that need wheels built from source.

### Install from source

The build first tries an approved, checksum-pinned AppVeyor executable for the
exact validator source revision. No AppVeyor account or token is needed. If no
compatible artifact is available, install [Rust 1.90+ with Cargo](https://rustup.rs/)
and a native linker/toolchain for the source-build fallback.
Use a GitHub checkout with the public validator submodule initialized:

```bash
git clone https://github.com/neuropoly/neuropoly-db.git
cd neuropoly-db
git submodule update --init --recursive -- tools/bids-validator-rust
uv tool install .
```

This acquires or builds the native binary outside the submodule and includes it
in the installed package. An npdb source distribution also includes the required Rust
sources and can build without Git metadata. GitHub-generated source ZIP/tar
archives omit submodules and cannot substitute for an npdb sdist. Source builds
need Cargo dependencies downloaded or cached when falling back to compilation.
Artifact-backed builds include a pinned snapshot of dependency license notices
and do not need Cargo to assemble the bundle.

For editable development, use `uv sync --dev`, then
`bash scripts/install_bids_validator.sh`. Editable installation does not
automatically compile Rust; see the wiki's development environment setup.

To build distributable wheels, follow the wiki's
[Building wheels](https://github.com/neuropoly/neuropoly-db/wiki/Development-Building-Wheels)
guide.

On Linux, the local builder can produce both Linux wheels using Docker (Docker
must be able to run ARM64 containers for the ARM64 target):

```bash
python tools/build_wheels.py --targets linux-x86_64 linux-arm64 --verify
```

It can also cross-build the Windows x86-64 wheel with
[cargo-xwin](https://github.com/rust-cross/cargo-xwin) and Clang/LLVM. Install
Rust 1.90+, `uv`, Docker if also building Linux targets, and cargo-xwin
(`cargo install --locked cargo-xwin`), Clang/LLVM, and the Rust target
(`rustup target add x86_64-pc-windows-msvc`). cargo-xwin downloads the Windows
SDK and CRT; review and accept its Microsoft license terms. Initialize the
pinned validator submodule before building. For example, to build all three
targets:

```bash
python tools/build_wheels.py \
  --targets linux-x86_64 linux-arm64 windows-x86_64 \
  --cross-compile-windows
```

The Windows cross-build performs the PE architecture/runtime audit, but cannot
run the installed-wheel acceptance test on Linux. To verify that exact wheel,
copy its output directory to a Windows x86-64 checkout with the same project
version and sources, prepare a Windows dependency wheelhouse, and run:

```powershell
python -m pip wheel --prefer-binary `
  --wheel-dir dist/wheels/windows-x86_64/wheelhouse `
  dist/wheels/windows-x86_64/npdb-*.whl
python tools/build_wheels.py --targets windows-x86_64 `
  --output dist/wheels/windows-x86_64 --verify-existing
```

Approved artifacts can also be packaged into foreign-platform wheels on Linux:

```bash
python tools/build_wheels.py --targets macos-x86_64 macos-arm64
```

The current macOS artifact is a universal Intel/Apple Silicon executable; the
builder keeps separate platform-specific wheels and audits both slices without
requiring Apple tools on Linux. Native acceptance must still run on the target
OS/architecture using `--verify-existing` and a target-specific dependency
wheelhouse. `--verify` rejects foreign desktop targets.

Artifact availability is not guaranteed: no refresh schedule is documented in
the upstream repository, and AppVeyor artifacts expire. Approved archives are
cached under `build/artifact-cache` (override with `NPDB_VALIDATOR_CACHE`) and
rechecked on reuse. Missing/expired downloads and incompatible binaries report
the reason before falling back to source builds. Checksum mismatches are hard
errors, not silently ignored.

For the currently pinned revision, the Linux AppVeyor executable requires glibc
2.30 and the Windows executable requires an unbundled `vcruntime140.dll`. Both
are rejected by our existing portability requirements, so those targets use
the source fallback. Linux ARM64 has no approved artifact. The macOS artifact
passes the architecture, deployment-target, and system-library checks.

If a macOS artifact is unavailable on Linux, the build fails with instructions
to use a matching macOS host; no Apple cross-toolchain is installed
automatically. Windows source fallback on Linux still requires
`--cross-compile-windows` and cargo-xwin. Toolchains are required only when
falling back, not before trying a desktop artifact.

Use `--source-only` with the wheel builder, or set
`NPDB_VALIDATOR_SOURCE_ONLY=1` for any build/install entry point, to disable
artifact acquisition and exercise the existing source chain. This also avoids
artifact network access for offline source builds; Cargo dependencies and
Python build requirements must already be cached.

The devcontainer runs `uv sync --dev --all-extras` during post-create setup,
including when reusing an existing virtual environment. To restore its project
and development dependencies without rebuilding the container, run that command
from the repository root.

## 4. Check it works

```bash
npdb --help
```

You should see the list of `npdb` commands.

> [!TIP]
> `npdb: command not found`? Run `uv tool update-shell`, then close and reopen your terminal.

## Update or remove

```bash
uv tool install --reinstall "./npdb-<version>-py3-none-<platform>.whl"
uv tool uninstall npdb
```

For source installs, update your checkout and its validator submodule, then
rerun `uv tool install --reinstall .`. The build never patches the submodule's
Cargo files or Rust sources and never places Cargo outputs there.

Next: [Find data](find-data.md)

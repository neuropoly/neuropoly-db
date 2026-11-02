# Install npdb

Use a matching prebuilt wheel to install npdb with its pinned native Rust BIDS
validator, without installing Rust yourself. Source installations require Rust
and an initialized validator submodule.

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

Install [Rust 1.90+ with Cargo](https://rustup.rs/) and a native linker/toolchain.
Then use a GitHub checkout with the public validator submodule initialized:

```bash
git clone https://github.com/neuropoly/neuropoly-db.git
cd neuropoly-db
git submodule update --init --recursive -- tools/bids-validator-rust
uv tool install .
```

This builds the native binary outside the submodule and includes it in the
installed package. An npdb source distribution also includes the required Rust
sources and can build without Git metadata. GitHub-generated source ZIP/tar
archives omit submodules and cannot substitute for an npdb sdist. Source builds
need Cargo dependencies downloaded or cached.

For editable development, use `uv sync --dev`, then
`bash scripts/install_bids_validator.sh`. Editable installation does not
automatically compile Rust; see the wiki's development environment setup.

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

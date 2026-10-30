# Install npdb

This procedure installs `npdb[all]` commands from a GitHub
checkout.

- Network access is required to download the source,
toolchains, and dependencies.
- Provider credentials
and access are configured separately: see [Get access](get-access.md).

The commands below cover Ubuntu/Debian and macOS. For Windows, use an Ubuntu environment under
[WSL](https://learn.microsoft.com/windows/wsl/install) and follow the Ubuntu
steps. If installation fails, [open an issue](https://github.com/neuropoly/neuropoly-db/issues/new/choose)
with the failing command and error output.

## 1. Install system dependencies

### Ubuntu/Debian

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl build-essential git git-annex
```

### macOS

With [Homebrew](https://brew.sh) installed,
install git and git-annex:

```bash
brew install git git-annex
```

## 2. Install uv

```bash
curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh -o install-uv.sh
sh install-uv.sh
rm install-uv.sh
```

**Close and reopen your terminal**, then check:

```bash
uv --version
```

## 3. Install Rust and Cargo

```bash
curl --proto '=https' --tlsv1.2 -LsSf https://sh.rustup.rs -o install-rust.sh
sh install-rust.sh -y --profile minimal --default-toolchain 1.90.0
rm install-rust.sh
```

**Close and reopen your terminal**, then check both tools:

```bash
rustc --version
cargo --version
```

## 4. Get the source and install npdb

From a directory where you want to keep the source checkout:

```bash
git clone https://github.com/neuropoly/neuropoly-db.git
cd neuropoly-db
git submodule update --init --recursive -- tools/bids-validator-rust
uv tool install --python 3.12 ".[all]"
```

Linux wheel builds audit the native validator before assigning a portable tag.
Manylinux wheels allow standard glibc runtime libraries, including
`libpthread.so.0` and `libdl.so.2`, while requiring glibc 2.28 or older.
Musllinux wheels reject glibc dependencies.

The wheel build matrix does not support native Windows ARM64 installations:
the required `bagel` dependency uses `bids2table`, whose `pyarrow` dependency
has no compatible binary wheel. Windows x86-64 builds remain supported.

## 5. Check the installation

```bash
git --version
git annex version
npdb --help
bids-repair --help
```

The first two commands should print version information; the last two should
print their respective command help. These are local installation checks, not a
test of credentials, server access, or a dataset download.

> [!TIP]
> If `npdb` or `bids-repair` is not found, run `uv tool update-shell`, then close
> and reopen your terminal and repeat the checks.

## Update or remove

To update this source-based installation, return to the GitHub checkout created
in step 4, then run:

```bash
git pull --ff-only
git submodule update --init --recursive -- tools/bids-validator-rust
uv tool install --reinstall --python 3.12 ".[all]"
```

To remove the uv-managed npdb installation and its command entry points:

```bash
uv tool uninstall npdb
```

Next: [Find data](find-data.md)

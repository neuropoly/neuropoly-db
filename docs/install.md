# Install npdb

**Use a matching prebuilt wheel** to install npdb with its **pinned native Rust BIDS
validator**:

## 1. Install the tools npdb relies on

`npdb` requires **git** and **git-annex**:

- **Ubuntu/Debian:**

  ```bash
  sudo apt-get install -y git git-annex
  ```

- **macOS** (with [Homebrew](https://brew.sh)):

  ```bash
  brew install git git-annex
  ```

## 2. Install uv

Install [uv](https://docs.astral.sh/uv/) python manager:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Close and reopen your terminal** afterwards.

## 3. Install npdb

### From prebuilt wheels

Download the wheel for your platform from [npdb releases](https://github.com/neuropoly/neuropoly-db/releases). Extract the artifact and install
the wheel file (replace the example filename with the downloaded filename):

```bash
uv pip install npdb
```

npdb supports a limited set of platforms:

- Linux x86-64/ARM64
- macOS 11+ Intel/Apple Silicon
- Windows x86-64

Wheels contain:

- The BIDS validator executable
- Embedded BIDS validation schemas

### Install from source

#### Prerequisites

- [Rust 1.90+ with Cargo](https://rustup.rs/)
- Build tools
  - On Linux: `sudo apt install -y build-essential`
  - On macOS: `brew install llvm`

#### Installation

Clone the repository and initialize necessary submodules:

```bash
git clone https://github.com/neuropoly/neuropoly-db.git
cd neuropoly-db
git submodule update --init --recursive -- tools/bids-validator-rust
uv tool install .
```

Build a wheel for your platform:

  ```bash
  python tools/build_wheels.py --targets linux-x86_64 --verify
  ```

Finally, install the wheel in your python environment:

```bash
uv tool install ./dist/*.whl
```

## 4. Check it works

```bash
npdb --help
```

You should see the list of `npdb` commands.

> [!TIP]
> `npdb: command not found`? Run `uv tool update-shell`, then close and reopen your terminal.

Next: [Find data](find-data.md)

# Native Conversion and Validation Tools

`npdb` uses `dcm2niix` to convert MIDRC DICOM series and the Rust-only
`bids-validator-rust` executable to validate datasets before Neurobagel imaging
table generation. Missing tools cause actionable errors; no JavaScript, Deno,
or Python dataset-validator fallback is invoked.

## Local Installation

Install Python dependencies using the [project installation steps](../../README.md#installation).
Install [Rust 1.85+ and Cargo](https://rustup.rs/) and a native linker/toolchain.
Then install the DICOM converter using your system package manager:

```bash
# Debian/Ubuntu
sudo apt-get update
sudo apt-get install -y dcm2niix build-essential

# macOS alternative
brew install dcm2niix
```

Build the pinned validator from the repository root:

```bash
bash scripts/install_bids_validator.sh
.venv/bin/bids-validator-rust --version
dcm2niix --version
```

The script defaults to `$VIRTUAL_ENV` or the project's `.venv`. Pass a different
installation prefix as its first argument if needed. It reuses the matching
installed binary or builds with `cargo install --locked`; Rust dependencies
are pinned by [Cargo.toml](../../tools/bids-validator-rust/Cargo.toml) and
[Cargo.lock](../../tools/bids-validator-rust/Cargo.lock). `uv sync` and
`requirements.txt` install Python packages, not these native executables.

## Containers

Rebuild the VS Code dev container after pulling these changes. The
[workspace Dockerfile](../../.devcontainer/Dockerfile) builds the validator with
Rust 1.90.0 and installs Debian's `dcm2niix` package in the Python workspace
image. Container creation makes the validator available in the virtual
environment. Neither Node nor Deno is required for validation. Neurobagel API,
database, and UI services do not run these CLI tools and are unchanged.

The Docker build context contains only the validator source and build files;
credentials, environment files, dataset caches, and extracted images are excluded.

## Validation

```bash
.venv/bin/bids-validator-rust /path/to/bids > validation.json
```

The runner checks the raw root and each immediate derivative dataset. It emits
JSON and exits nonzero for errors. `npdb convert bagel` uses this same executable
before Neurobagel's imaging-table export, replacing its PyBIDS validation gate;
the upstream table-generation and indexing libraries remain in use.

The pinned `bids-validate 0.0.3` library is a lightweight structural validator.
It checks dataset descriptions, subject-file naming, and derivative provenance;
it does **not** establish full sidecar or NIfTI semantic compliance. A zero-error
report must not be described as full BIDS semantic certification. Warnings are
retained in the report. The Rust runner is separately licensed GPL-3.0-or-later,
matching its underlying library.
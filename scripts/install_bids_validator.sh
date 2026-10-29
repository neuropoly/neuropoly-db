#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
prefix=${1:-${VIRTUAL_ENV:-$root/.venv}}
expected='bids-validator-rust 0.0.3 (bids-validate 0.0.3)'

for executable in "$prefix/bin/bids-validator-rust" "$(command -v bids-validator-rust || true)" /usr/local/bin/bids-validator-rust; do
    if [[ -x "$executable" ]] && [[ "$("$executable" --version 2>/dev/null)" == "$expected" ]]; then
        if [[ "$executable" != "$prefix/bin/bids-validator-rust" ]]; then
            mkdir -p "$prefix/bin"
            install -m 755 "$executable" "$prefix/bin/bids-validator-rust"
        fi
        printf 'Rust BIDS validator already installed: %s\n' "$executable"
        exit 0
    fi
done

if ! command -v cargo >/dev/null 2>&1; then
    printf 'Install Rust 1.85+ with Cargo before running this script.\n' >&2
    exit 1
fi

cargo install --locked --path "$root/tools/bids-validator-rust" --root "$prefix" --force
"$prefix/bin/bids-validator-rust" --version

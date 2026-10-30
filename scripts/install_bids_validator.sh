#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
prefix=${1:-${VIRTUAL_ENV:-$root/.venv}}
python=${PYTHON:-python3}
expected='bids-validator 2.4.1 commit 94ea971 rust 0.1.20260517'
commit='rust git commit b5c11828e104be88034ecccbe220ad1a20c72dfe'

for executable in "$prefix/bin/bids-validator" "$(command -v bids-validator || true)" /usr/local/bin/bids-validator; do
    if [[ -x "$executable" ]]; then
        version=$("$executable" --version)
        if [[ "$version" == "$expected"$'\n'* ]] && grep -Fxq "$commit" <<< "$version"; then
            if [[ "$executable" != "$prefix/bin/bids-validator" ]]; then
                mkdir -p "$prefix/bin"
                install -m 755 "$executable" "$prefix/bin/bids-validator"
            fi
            printf 'Pinned Rust validator already installed: %s\n' "$executable"
            exit 0
        fi
    fi
done

"$python" "$root/tools/build_validator.py" --output "$prefix/bin"
"$prefix/bin/bids-validator" --version

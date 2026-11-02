#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
prefix=${1:-${VIRTUAL_ENV:-$root/.venv}}
python=${PYTHON:-$prefix/bin/python}
if [[ ! -x "$python" ]]; then
    python=$(command -v python3) || {
        printf 'Python 3.12+ is required to install the native validator.\n' >&2
        exit 1
    }
fi
if ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    printf 'Python 3.12+ is required to install the native validator.\n' >&2
    exit 1
fi
exec "$python" "$root/tools/build_validator.py" --install "$prefix"

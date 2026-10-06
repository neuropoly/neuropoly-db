import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def validate_bids_dataset(dataset: str | Path) -> dict[str, Any]:
    executable = shutil.which("bids-validator-rust")
    if executable is None:
        candidate = Path(sys.executable).parent / "bids-validator-rust"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            executable = str(candidate)
    if executable is None:
        raise RuntimeError(
            "The Rust BIDS validator is required. Run "
            "bash scripts/install_bids_validator.sh after installing Rust 1.85+."
        )
    result = subprocess.run(
        [executable, str(Path(dataset).resolve())],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"Rust BIDS validation failed: {result.stdout or result.stderr}"
        )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Rust BIDS validator returned invalid JSON.") from exc
    if (
        not isinstance(report, dict)
        or not isinstance(report.get("datasets"), list)
        or not report["datasets"]
        or any(
            not isinstance(item, dict) or item.get("errors") != 0
            for item in report["datasets"]
        )
    ):
        raise RuntimeError(f"Rust BIDS validation failed: {result.stdout}")
    return report

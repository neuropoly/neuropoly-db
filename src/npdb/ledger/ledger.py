"""
Unified run ledger for npdb.

Tracks both successful and failed annotation runs in a single JSON file.
Replaces the per-run ``phenotypes_provenance.json`` sidecar for
``gitea2bagel`` output directories.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from npdb.annotation.provenance import ProvenanceReport
from npdb.external.neurobagel.errors import BagelCLIError

LEDGER_VERSION = "1"


class RunLedger:
    """
    Append-only JSON ledger stored at *path*.

    Schema::

        {
          "ledger_version": "1",
          "entries": [ ... ]
        }

    Each entry has at minimum:
      - ``status``: ``"success"`` | ``"failure"``
      - ``process``: name of the invoking command (e.g. ``"gitea2bagel"``)
      - ``dataset``: dataset name
      - ``timestamp``: ISO-8601 UTC string
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, entry: Dict[str, Any]) -> None:
        """Load existing ledger (or create new), append *entry*, write atomically."""
        ledger = self._load()
        ledger["entries"].append(entry)
        self._save(ledger)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                with open(self.path) as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):
                pass
        return {"ledger_version": LEDGER_VERSION, "entries": []}

    def _save(self, ledger: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        try:
            with open(tmp, "w") as fh:
                json.dump(ledger, fh, indent=2, default=str)
            tmp.replace(self.path)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise


# ------------------------------------------------------------------
# Entry builders
# ------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def success_entry_from_report(
    dataset: str,
    process: str,
    report: ProvenanceReport,
) -> Dict[str, Any]:
    """Build a ledger success entry from a :class:`ProvenanceReport`."""
    return {
        "status": "success",
        "process": process,
        "dataset": dataset,
        "timestamp": _now_iso(),
        "mode": report.mode,
        "mapping_source_counts": report.mapping_source_counts,
        "confidence_distribution": report.confidence_distribution.model_dump(),
        "per_column": {
            col: prov.model_dump()
            for col, prov in report.per_column.items()
        },
        "warnings": report.warnings,
        "ai_provider": report.ai_provider,
        "ai_model": report.ai_model,
    }


def minimal_success_entry(dataset: str, process: str, mode: str) -> Dict[str, Any]:
    """Build a minimal success entry (manual mode — no provenance report)."""
    return {
        "status": "success",
        "process": process,
        "dataset": dataset,
        "timestamp": _now_iso(),
        "mode": mode,
    }


def failure_entry(
    dataset: str,
    process: str,
    err: BagelCLIError,
    classified: list,
) -> Dict[str, Any]:
    """Build a ledger failure entry from a :class:`BagelCLIError`."""
    problem_name = classified[0]["problem"] if classified else "Unknown error"
    problem_description = classified[0]["description"] if classified else str(
        err)
    resolution_steps = classified[0]["fix_steps"] if classified else []
    return {
        "status": "failure",
        "process": process,
        "dataset": dataset,
        "timestamp": _now_iso(),
        "bagel_command": err.command,
        "problem_name": problem_name,
        "problem_description": problem_description,
        "resolution_steps": resolution_steps,
        "raw_output_snippet": err.plain_output[:500],
    }

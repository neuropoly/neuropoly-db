"""
Unified run ledger for npdb.

Tracks both successful and failed annotation runs in a single JSON file.
Replaces the per-run ``phenotypes_provenance.json`` sidecar for
``gitea2bagel`` output directories.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from npdb.annotation.provenance import ProvenanceReport
from npdb.external.neurobagel.errors import BagelCLIError

LEDGER_VERSION = "1"

# ---------------------------------------------------------------------------
# Problem-name constants
# ---------------------------------------------------------------------------

PROBLEM_GIT_CLONE_FAILURE = "git_clone_failure"
PROBLEM_MISSING_PARTICIPANTS_TSV = "missing_participants_tsv"
PROBLEM_DESCRIPTION_EXTENSION_FAILURE = "description_extension_failure"
PROBLEM_PREFLIGHT_FAILURE = "preflight_failure"
PROBLEM_ANNOTATION_FAILURE = "annotation_failure"


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
    preprocessing_warnings: Optional[List[str]] = None,
    subject_alignment_warnings: Optional[List[str]] = None,
    vocab_extension_pending: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a ledger success entry from a :class:`ProvenanceReport`."""
    entry: Dict[str, Any] = {
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
    if preprocessing_warnings:
        entry["preprocessing_warnings"] = preprocessing_warnings
    if subject_alignment_warnings:
        entry["subject_alignment_warnings"] = subject_alignment_warnings
    if vocab_extension_pending:
        entry["vocab_extension_pending"] = vocab_extension_pending
    return entry


def minimal_success_entry(
    dataset: str,
    process: str,
    mode: str,
    preprocessing_warnings: Optional[List[str]] = None,
    subject_alignment_warnings: Optional[List[str]] = None,
    vocab_extension_pending: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a minimal success entry (manual mode — no provenance report)."""
    entry: Dict[str, Any] = {
        "status": "success",
        "process": process,
        "dataset": dataset,
        "timestamp": _now_iso(),
        "mode": mode,
    }
    if preprocessing_warnings:
        entry["preprocessing_warnings"] = preprocessing_warnings
    if subject_alignment_warnings:
        entry["subject_alignment_warnings"] = subject_alignment_warnings
    if vocab_extension_pending:
        entry["vocab_extension_pending"] = vocab_extension_pending
    return entry


def failure_entry(
    dataset: str,
    process: str,
    err: BagelCLIError,
    classified: list,
    preprocessing_warnings: Optional[List[str]] = None,
    subject_alignment_warnings: Optional[List[str]] = None,
    vocab_extension_pending: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a ledger failure entry from a :class:`BagelCLIError`."""
    problem_name = classified[0]["problem"] if classified else "Unknown error"
    context = classified[0].get("context", {}) if classified else {}
    base_description = classified[0]["description"] if classified else str(err)
    if context:
        context_detail = "; ".join(f"{k}={v}" for k, v in context.items())
        problem_description = f"{base_description} ({context_detail})"
    else:
        problem_description = base_description
    resolution_steps = classified[0]["fix_steps"] if classified else []
    entry: Dict[str, Any] = {
        "status": "failure",
        "process": process,
        "dataset": dataset,
        "timestamp": _now_iso(),
        "bagel_command": err.command,
        "problem_name": problem_name,
        "problem_description": problem_description,
        "auto_fixable_steps": [s["action"] for s in resolution_steps if s.get("auto_fixable")],
        "resolution_steps": resolution_steps,
        "raw_output_snippet": err.plain_output[:500],
    }
    if preprocessing_warnings:
        entry["preprocessing_warnings"] = preprocessing_warnings
    if subject_alignment_warnings:
        entry["subject_alignment_warnings"] = subject_alignment_warnings
    if vocab_extension_pending:
        entry["vocab_extension_pending"] = vocab_extension_pending
    return entry


def generic_failure_entry(
    dataset: str,
    process: str,
    problem_name: str,
    problem_description: str,
    fix_steps: Optional[List] = None,
    raw_snippet: str = "",
    preprocessing_warnings: Optional[List[str]] = None,
    subject_alignment_warnings: Optional[List[str]] = None,
    vocab_extension_pending: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a ledger failure entry from a plain error (no BagelCLIError)."""
    steps = fix_steps or []
    # Normalise plain strings to step dicts for callers that still pass strings
    normalised = [
        s if isinstance(s, dict) else {
            "action": s, "detail": "", "auto_fixable": False}
        for s in steps
    ]
    entry: Dict[str, Any] = {
        "status": "failure",
        "process": process,
        "dataset": dataset,
        "timestamp": _now_iso(),
        "bagel_command": None,
        "problem_name": problem_name,
        "problem_description": problem_description,
        "auto_fixable_steps": [s["action"] for s in normalised if s.get("auto_fixable")],
        "resolution_steps": normalised,
        "raw_output_snippet": raw_snippet[:500],
    }
    if preprocessing_warnings:
        entry["preprocessing_warnings"] = preprocessing_warnings
    if subject_alignment_warnings:
        entry["subject_alignment_warnings"] = subject_alignment_warnings
    if vocab_extension_pending:
        entry["vocab_extension_pending"] = vocab_extension_pending
    return entry

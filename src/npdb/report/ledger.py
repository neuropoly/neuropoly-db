"""
Run ledger for tracking annotation run outcomes and warnings.

Provides:
- RunLedger: accumulates warnings and outcome data for a single run.
- LedgerObserver: subscribes to resolution events and forwards warnings
  to a RunLedger instance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from npdb.automation.mappings.resolvers import ResolvedMapping


@dataclass
class RunLedger:
    """
    Tracks outcomes and warnings accumulated during a dataset annotation run.

    A new RunLedger should be created per run and optionally persisted to
    *path* at the end of the run via :meth:`flush`.

    Attributes:
        path:       Optional file path where the ledger will be written.
        warnings:   Warning messages emitted by observers during the run.
        errors:     Error messages recorded when a step fails.
        outcome:    High-level result -- 'success', 'failure',
                    or 'pending' while the run is in progress.
    """

    path: Path | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    outcome: str = "pending"

    def record_success(self) -> None:
        """Mark the run as successful."""
        self.outcome = "success"

    def record_failure(self, reason: str) -> None:
        """Mark the run as failed and record the reason as an error."""
        self.outcome = "failure"
        self.errors.append(reason)

    def flush(self) -> None:
        """
        Persist the ledger to :attr:`path` as JSON if a path is configured.

        No-op when :attr:`path` is ``None``.
        """
        if self.path is None:
            return
        payload = {
            "outcome": self.outcome,
            "warnings": self.warnings,
            "errors": self.errors,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2))


class LedgerObserver:
    """
    Observer adapter that forwards resolution warnings to a :class:`RunLedger`.

    Register via :meth:`npdb.managers.annotation.AnnotationManager.add_observer`
    so that any warning raised during column resolution is automatically
    captured in the ledger.
    """

    def __init__(self, ledger: RunLedger) -> None:
        self._ledger = ledger

    def on_resolved(self, column_name: str, mapping: "ResolvedMapping") -> None:
        """No-op -- successful resolutions are not tracked in the ledger."""

    def on_warning(self, message: str) -> None:
        """Forward *message* to the ledger's warning list."""
        self._ledger.warnings.append(message)

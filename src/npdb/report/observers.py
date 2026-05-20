# ---------------------------------------------------------------------------
# Observer protocol & built-in implementation
# ---------------------------------------------------------------------------

from typing import Protocol, runtime_checkable

from npdb.automation.mappings.resolvers import ResolvedMapping
from npdb.report.provenance import ProvenanceReport


@runtime_checkable
class ResolutionObserver(Protocol):
    """
    Protocol for objects that want to be notified of column resolution events.

    Implement both methods and register via AnnotationManager.add_observer().
    """

    def on_resolved(self, column_name: str, mapping: ResolvedMapping) -> None:
        """Called when a column is successfully resolved above the threshold."""
        ...

    def on_warning(self, message: str) -> None:
        """Called when a low-confidence or otherwise noteworthy event occurs."""
        ...


class ProvenanceObserver:
    """
    Built-in observer that records resolution events into a ProvenanceReport.

    Wraps add_column_provenance() and provenance.warnings.append() so that
    AnnotationManager.resolve_and_track() never touches provenance directly.
    """

    def __init__(self, provenance: ProvenanceReport) -> None:
        self.provenance = provenance

    def on_resolved(self, column_name: str, mapping: ResolvedMapping) -> None:
        self.provenance.add_column_provenance(
            column_name=mapping.column_name,
            source=mapping.source,
            confidence=mapping.confidence,
            variable=mapping.mapped_variable,
            rationale=mapping.rationale,
        )

    def on_warning(self, message: str) -> None:
        self.provenance.warnings.append(message)

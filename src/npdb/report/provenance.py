"""
Provenance tracking and reporting for annotation automation.

Captures the lineage and confidence of each column mapping decision for
auditability and retrospection, especially important for full-auto mode.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from uuid import uuid4

from pydantic import BaseModel, Field

from npdb.annotation import AnnotationMode


class MappingSource(str, Enum):
    STATIC = "static"
    DETERMINISTIC = "deterministic"
    AI = "ai"
    MANUAL = "manual"
    UNRESOLVED = "unresolved"


class ColumnProvenance(BaseModel):
    """Provenance metadata for a single column mapping."""

    column_name: str
    source: MappingSource

    confidence: float = Field(ge=0.0, le=1.0)

    variable: str | None = Field(
        default=None, description="Mapped standardized variable"
    )
    format: str | None = Field(default=None, description="For continuous variables")
    rationale: str = Field(..., description="Explanation of the mapping decision")
    ai_model: str | None = Field(default=None, description="LLM model if source=ai")
    ai_model_version: str | None = Field(
        default=None, description="Model version if source=ai"
    )


class ConfidenceDistribution(BaseModel):
    """Distribution of confidence scores across mappings."""

    high: list[float] = Field(default=[], description="Scores in [0.85, 1.0]")
    medium: list[float] = Field(default=[], description="Scores in [0.7, 0.84]")
    low: list[float] = Field(default=[], description="Scores in [0.5, 0.69]")
    unresolved: int = Field(default=0, description="Count of unresolved columns")


class ProvenanceReport(BaseModel):
    """Complete provenance report for an annotation run."""

    run_id: str = Field(default_factory=lambda: str(uuid4()))
    mode: AnnotationMode
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    dataset_name: str | None = Field(default=None)

    # Mapping source summary
    mapping_source_counts: dict[str, int] = Field(
        default_factory=lambda: {"static": 0, "deterministic": 0, "ai": 0, "manual": 0}
    )

    # Confidence distribution
    confidence_distribution: ConfidenceDistribution = Field(
        default_factory=ConfidenceDistribution
    )

    # Per-column provenance
    per_column: dict[str, ColumnProvenance] = Field(
        default_factory=dict, description="Mapping provenance for each column"
    )

    # Warnings and notes
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings or issues encountered during annotation",
    )

    # AI configuration (if applicable)
    ai_provider: str | None = Field(default=None)
    ai_model: str | None = Field(default=None)
    ai_threshold: float | None = Field(default=None)

    # -----------------------------------------------------------------------
    # Instance methods
    # -----------------------------------------------------------------------

    def add_column_provenance(
        self,
        column_name: str,
        source: str,
        confidence: float,
        variable: str | None = None,
        format: str | None = None,
        rationale: str = "No rationale provided",
        ai_model: str | None = None,
        ai_model_version: str | None = None,
    ) -> None:
        """Add or update column provenance (see module-level function for docs)."""
        col_prov = ColumnProvenance(
            column_name=column_name,
            source=source,
            confidence=confidence,
            variable=variable,
            format=format,
            rationale=rationale,
            ai_model=ai_model,
            ai_model_version=ai_model_version,
        )

        is_update = column_name in self.per_column
        self.per_column[column_name] = col_prov

        self.mapping_source_counts[source] = (
            self.mapping_source_counts.get(source, 0) + 1
        )

        if is_update:
            self.confidence_distribution = compute_confidence_distribution(
                self.per_column
            )
        elif col_prov.source != MappingSource.MANUAL:
            _bucket_confidence(self.confidence_distribution, confidence)

    def add_warning(self, warning: str) -> None:
        """Append *warning* to the report's warning list (deduplicating)."""
        if warning not in self.warnings:
            self.warnings.append(warning)

    def save(self, output_path: Path) -> None:
        """Save the report to *output_path* as formatted JSON."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(self.model_dump(mode="json"), f, indent=2, default=str)

    @classmethod
    def from_file(cls, path: Path) -> "ProvenanceReport":
        """Load and reconstruct a :class:`ProvenanceReport` from a JSON file."""
        if not path.exists():
            raise FileNotFoundError(f"Provenance file not found: {path}")
        with open(path, "r") as f:
            data = json.load(f)
        return cls(**data)


def _bucket_confidence(dist: ConfidenceDistribution, conf: float) -> None:
    """Place a single confidence score into the correct bucket of *dist* in-place."""
    if conf >= 0.85:
        dist.high.append(conf)
    elif conf >= 0.7:
        dist.medium.append(conf)
    elif conf >= 0.5:
        dist.low.append(conf)
    else:
        dist.unresolved += 1


def compute_confidence_distribution(
    per_column: dict[str, ColumnProvenance],
) -> ConfidenceDistribution:
    """
    Compute confidence distribution from per-column mappings.

    Manual mappings are excluded because they carry no meaningful
    confidence score (they are human-verified by definition).

    Args:
        per_column: Mapping of column names to provenance records

    Returns:
        ConfidenceDistribution with scores bucketed by range
    """
    dist = ConfidenceDistribution()
    for col_prov in per_column.values():
        if col_prov.source != MappingSource.MANUAL:
            _bucket_confidence(dist, col_prov.confidence)
    return dist

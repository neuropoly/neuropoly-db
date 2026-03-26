"""
Provenance tracking and reporting for annotation automation.

Captures the lineage and confidence of each column mapping decision for
auditability and retrospection, especially important for full-auto mode.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Literal
from uuid import uuid4
from pydantic import BaseModel, Field


class ColumnProvenance(BaseModel):
    """Provenance metadata for a single column mapping."""
    column_name: str
    source: Literal["static", "deterministic", "ai", "manual"]
    confidence: float = Field(ge=0.0, le=1.0)
    variable: Optional[str] = Field(
        default=None, description="Mapped standardized variable")
    format: Optional[str] = Field(
        default=None, description="For continuous variables")
    rationale: str = Field(...,
                           description="Explanation of the mapping decision")
    ai_model: Optional[str] = Field(
        default=None, description="LLM model if source=ai")
    ai_model_version: Optional[str] = Field(
        default=None, description="Model version if source=ai")


class ConfidenceDistribution(BaseModel):
    """Distribution of confidence scores across mappings."""
    high: List[float] = Field(default=[], description="Scores in [0.85, 1.0]")
    medium: List[float] = Field(
        default=[], description="Scores in [0.7, 0.84]")
    low: List[float] = Field(default=[], description="Scores in [0.5, 0.69]")
    unresolved: int = Field(
        default=0, description="Count of unresolved columns")


class ProvenanceReport(BaseModel):
    """Complete provenance report for an annotation run."""
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    mode: Literal["manual", "assist", "auto", "full-auto"]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    dataset_name: Optional[str] = Field(default=None)

    # Mapping source summary
    mapping_source_counts: Dict[str, int] = Field(
        default_factory=lambda: {"static": 0,
                                 "deterministic": 0, "ai": 0, "manual": 0}
    )

    # Confidence distribution
    confidence_distribution: ConfidenceDistribution = Field(
        default_factory=ConfidenceDistribution
    )

    # Per-column provenance
    per_column: Dict[str, ColumnProvenance] = Field(
        default_factory=dict,
        description="Mapping provenance for each column"
    )

    # Warnings and notes
    warnings: List[str] = Field(
        default_factory=list,
        description="Warnings or issues encountered during annotation"
    )

    # AI configuration (if applicable)
    ai_provider: Optional[str] = Field(default=None)
    ai_model: Optional[str] = Field(default=None)
    ai_threshold: Optional[float] = Field(default=None)


def compute_confidence_distribution(
    per_column: Dict[str, ColumnProvenance]
) -> ConfidenceDistribution:
    """
    Compute confidence distribution from per-column mappings.

    Args:
        per_column: Mapping of column names to provenance records

    Returns:
        ConfidenceDistribution with scores bucketed by range
    """
    dist = ConfidenceDistribution()

    for col_prov in per_column.values():
        if col_prov.source == "manual":
            continue

        conf = col_prov.confidence
        if conf >= 0.85:
            dist.high.append(conf)
        elif conf >= 0.7:
            dist.medium.append(conf)
        elif conf >= 0.5:
            dist.low.append(conf)

    return dist


def add_column_provenance(
    report: ProvenanceReport,
    column_name: str,
    source: Literal["static", "deterministic", "ai", "manual"],
    confidence: float,
    variable: Optional[str] = None,
    format: Optional[str] = None,
    rationale: str = "No rationale provided",
    ai_model: Optional[str] = None,
    ai_model_version: Optional[str] = None
) -> None:
    """
    Add or update column provenance in a report.

    Args:
        report: ProvenanceReport to update
        column_name: Name of the column
        source: Mapping source
        confidence: Confidence score [0, 1]
        variable: Mapped Neurobagel variable
        format: Format specification (for continuous)
        rationale: Explanation of the mapping
        ai_model: LLM model name (if source=ai)
        ai_model_version: LLM version (if source=ai)
    """
    col_prov = ColumnProvenance(
        column_name=column_name,
        source=source,
        confidence=confidence,
        variable=variable,
        format=format,
        rationale=rationale,
        ai_model=ai_model,
        ai_model_version=ai_model_version
    )

    report.per_column[column_name] = col_prov

    # Update source counts
    report.mapping_source_counts[source] = report.mapping_source_counts.get(
        source, 0) + 1

    # Recompute confidence distribution
    report.confidence_distribution = compute_confidence_distribution(
        report.per_column)


def add_warning(report: ProvenanceReport, warning: str) -> None:
    """
    Add a warning message to the provenance report.

    Args:
        report: ProvenanceReport to update
        warning: Warning message
    """
    if warning not in report.warnings:
        report.warnings.append(warning)


def save_provenance(report: ProvenanceReport, output_path: Path) -> None:
    """
    Save provenance report to JSON file.

    Args:
        report: ProvenanceReport to save
        output_path: Path to output file (typically phenotypes_provenance.json)
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(report.model_dump(mode="json"), f, indent=2, default=str)


def load_provenance(path: Path) -> ProvenanceReport:
    """
    Load provenance report from JSON file.

    Args:
        path: Path to provenance JSON file

    Returns:
        ProvenanceReport loaded from file

    Raises:
        FileNotFoundError: If file does not exist
    """
    if not path.exists():
        raise FileNotFoundError(f"Provenance file not found: {path}")

    with open(path, "r") as f:
        data = json.load(f)

    return ProvenanceReport(**data)

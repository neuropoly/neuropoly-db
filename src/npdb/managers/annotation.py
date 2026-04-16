"""
Abstract base class for annotation managers.

Shared parent for NeurobagelAnnotator and BIDSStandardizer, providing
common initialization (resolver, provenance) and shared resolution logic.
"""

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from npdb.annotation import AnnotationConfig
from npdb.annotation.provenance import ProvenanceReport, add_column_provenance
from npdb.automation.mappings.resolvers import MappingResolver, ResolvedMapping


class AnnotationManager(ABC):
    """
    Abstract manager for annotation-based workflows.

    Subclasses implement execute() and _save_outputs() for their specific
    output format (Neurobagel JSON-LD vs BIDS participants.json).
    """

    # Confidence thresholds by mode
    _CONFIDENCE_THRESHOLDS = {
        "manual": 0.0,
        "assist": 0.0,
        "auto": 0.7,
        "full-auto": 0.5,
    }

    def __init__(self, config: AnnotationConfig):
        self.config = config
        self._validate_config()
        self.resolver = self._init_resolver(config)
        self.provenance = self._init_provenance(config)

    def _init_resolver(self, config: AnnotationConfig) -> MappingResolver:
        """Create MappingResolver from config."""
        return MappingResolver(
            user_dictionary_path=config.phenotype_dictionary
        )

    def _init_provenance(self, config: AnnotationConfig) -> ProvenanceReport:
        """Create initial ProvenanceReport from config."""
        return ProvenanceReport(
            run_id=str(uuid.uuid4()),
            mode=config.mode,
            timestamp=datetime.now(timezone.utc).isoformat(),
            dataset_name="",
            mapping_source_counts={},
            per_column={},
            warnings=[],
        )

    def _validate_config(self) -> None:
        """Validate configuration for consistency. Override for extra checks."""
        if self.config.mode == "manual" and self.config.ai_provider:
            raise ValueError("AI provider not used in manual mode")

    def _get_confidence_threshold(self) -> float:
        """Return confidence threshold for the current mode."""
        return self._CONFIDENCE_THRESHOLDS.get(self.config.mode, 0.0)

    def resolve_and_track(
        self,
        column_names: List[str],
    ) -> Tuple[Dict[str, dict], List[ResolvedMapping]]:
        """
        Resolve columns and track provenance.

        Returns:
            Tuple of (annotations_dict, resolved_mappings).
            annotations_dict maps column_name -> {variable, source, confidence, rationale}
            for columns that met the confidence threshold.
        """
        threshold = self._get_confidence_threshold()
        resolved = self.resolver.resolve_columns(column_names)

        annotations_dict: Dict[str, dict] = {}
        for mapping in resolved:
            if mapping.source == "unresolved":
                continue
            # In auto/full-auto modes, skip below-threshold mappings
            if threshold > 0 and mapping.confidence < threshold and mapping.source != "static":
                self.provenance.warnings.append(
                    f"Low confidence mapping for '{mapping.column_name}': "
                    f"{mapping.rationale}"
                )
                continue

            add_column_provenance(
                self.provenance,
                column_name=mapping.column_name,
                source=mapping.source,
                confidence=mapping.confidence,
                variable=mapping.mapped_variable,
                rationale=mapping.rationale,
            )
            annotations_dict[mapping.column_name] = {
                "variable": mapping.mapped_variable,
                "source": mapping.source,
                "confidence": mapping.confidence,
                "rationale": mapping.rationale,
            }

        return annotations_dict, resolved

    @abstractmethod
    async def execute(self, input_path: Path, output_dir: Path) -> bool:
        """Execute the annotation/standardization workflow."""
        ...

    @abstractmethod
    async def _save_outputs(
        self,
        input_path: Path,
        output_dir: Path,
        annotations_dict: dict,
    ) -> None:
        """Save workflow outputs to disk."""
        ...

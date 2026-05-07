import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from npdb.annotation import AnnotationConfig
from npdb.automation.mappings.resolvers import MappingResolver, ResolvedMapping
from npdb.report.observers import ProvenanceObserver, ResolutionObserver
from npdb.report.provenance import ProvenanceReport


class Annotator(ABC):
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
        self._observers: List[ResolutionObserver] = []
        self.add_observer(ProvenanceObserver(self.provenance))

    def _init_resolver(self, config: AnnotationConfig) -> MappingResolver:
        """Create MappingResolver from config."""
        return MappingResolver(user_dictionary_path=config.phenotype_dictionary)

    def _init_provenance(self, config: AnnotationConfig) -> ProvenanceReport:
        """Create initial ProvenanceReport from config."""
        return ProvenanceReport(
            run_id=str(uuid.uuid4()),
            mode=config.mode,
            timestamp=datetime.now(timezone.utc),
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

    def add_observer(self, observer: ResolutionObserver) -> None:
        """Register an observer to receive resolution and warning events."""
        self._observers.append(observer)

    def _notify_observers(self, mapping: ResolvedMapping) -> None:
        """Notify all observers that *mapping* was successfully resolved."""
        for observer in self._observers:
            observer.on_resolved(mapping.column_name, mapping)

    def _notify_warning(self, message: str) -> None:
        """Notify all observers of a warning message."""
        for observer in self._observers:
            observer.on_warning(message)

    def resolve_and_track(
        self,
        column_names: List[str],
    ) -> Tuple[Dict[str, dict], List[ResolvedMapping]]:
        """
        Resolve columns and track provenance via registered observers.

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
            if (
                threshold > 0
                and mapping.confidence < threshold
                and mapping.source != "static"
            ):
                self._notify_warning(
                    f"Low confidence mapping for '{mapping.column_name}': "
                    f"{mapping.rationale}"
                )
                continue

            self._notify_observers(mapping)
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

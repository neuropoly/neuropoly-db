"""
Mapping resolver for precedence-based column-to-variable mapping.

Chains three layers of authority:
1. Static dictionary (highest confidence, repo-maintained)
2. Deterministic fuzzy matching (medium confidence, rule-based)
3. AI suggestions (lowest confidence, optional, deferred)

Resolver returns per-column mapping with source and confidence tracking.
"""

from typing import Dict, Optional, Any, List
from dataclasses import dataclass
from npdb.automation.mappings.solvers import load_static_mappings, load_user_mappings, merge_mappings
from npdb.annotation.matching import ColumnMatcher


@dataclass
class ResolvedMapping:
    """Result of resolving a column header to a phenotype variable."""
    column_name: str
    mapped_variable: str
    confidence: float
    source: str  # "static", "deterministic", "ai", or "unresolved"
    mapping_data: Dict[str, Any]
    rationale: str


class MappingResolver:
    """
    Resolves column headers to Neurobagel standardized variables via precedence chain.

    Precedence order:
    1. Static dictionary (user-supplied or built-in)
    2. Fuzzy matching against static dict keys/aliases
    3. AI suggestions (deferred to AnnotationManager; not handled here)
    """

    def __init__(
        self,
        user_dictionary_path: Optional[str] = None,
        exact_threshold: float = 1.0,
        fuzzy_threshold: float = 0.75
    ):
        """
        Initialize resolver with optional user dictionary override.

        Args:
            user_dictionary_path: Optional path to user-supplied phenotype_mappings.json.
            exact_threshold: Confidence threshold for exact matching (default 1.0).
            fuzzy_threshold: Confidence threshold for fuzzy matching (default 0.75).
        """
        # Load and merge mappings
        static_mappings = load_static_mappings()
        if user_dictionary_path:
            user_mappings = load_user_mappings(user_dictionary_path)
            self.mappings = merge_mappings(static_mappings, user_mappings)
        else:
            self.mappings = static_mappings

        # Initialize fuzzy matcher
        self.matcher = ColumnMatcher(self.mappings)

        # Thresholds for matching
        self.exact_threshold = exact_threshold
        self.fuzzy_threshold = fuzzy_threshold

        # Cache resolved mappings
        self._resolved_cache: Dict[str, ResolvedMapping] = {}

    def resolve_column(self, column_name: str) -> ResolvedMapping:
        """
        Resolve a column header to a phenotype variable via precedence chain.

        Attempts in order:
        1. Static dictionary (exact name match)
        2. Fuzzy matching against dict aliases
        3. Unresolved (deferred to AI or manual)

        Args:
            column_name: Column header from dataset.

        Returns:
            ResolvedMapping with source, confidence, and mapping data.
        """
        # Check cache
        if column_name in self._resolved_cache:
            return self._resolved_cache[column_name]

        # Attempt static dictionary match by exact name
        mapping_data = self.matcher.get_mapping_data(column_name)
        if mapping_data:
            resolved = ResolvedMapping(
                column_name=column_name,
                mapped_variable=mapping_data.get("variable", "unknown"),
                confidence=mapping_data.get("confidence", 0.95),
                source="static",
                mapping_data=mapping_data,
                rationale="Exact match in static dictionary"
            )
            self._resolved_cache[column_name] = resolved
            return resolved

        # Attempt fuzzy matching
        match_result = self.matcher.match_column(
            column_name,
            exact_threshold=self.exact_threshold,
            fuzzy_threshold=self.fuzzy_threshold
        )

        if match_result:
            mapping_key, confidence, match_source = match_result
            mapping_data = self.matcher.get_mapping_data(mapping_key)

            if mapping_data:
                resolved = ResolvedMapping(
                    column_name=column_name,
                    mapped_variable=mapping_data.get("variable", "unknown"),
                    confidence=confidence,
                    source="deterministic",
                    mapping_data=mapping_data,
                    rationale=f"Fuzzy match: '{column_name}' → '{mapping_key}' ({match_source}, score {confidence:.2f})"
                )
                self._resolved_cache[column_name] = resolved
                return resolved

        # No match found; unresolved
        resolved = ResolvedMapping(
            column_name=column_name,
            mapped_variable="",
            confidence=0.0,
            source="unresolved",
            mapping_data={},
            rationale=f"No static or fuzzy match found for '{column_name}'; requires AI suggestion or manual annotation"
        )
        self._resolved_cache[column_name] = resolved
        return resolved

    def resolve_columns(self, column_names: List[str]) -> List[ResolvedMapping]:
        """
        Resolve multiple column headers in batch.

        Args:
            column_names: List of column headers from dataset.

        Returns:
            List of ResolvedMapping for each column.
        """
        return [self.resolve_column(name) for name in column_names]

    def get_resolution_summary(self, resolved_mappings: List[ResolvedMapping]) -> Dict[str, Any]:
        """
        Generate summary statistics on resolution quality.

        Args:
            resolved_mappings: List of ResolvedMapping results.

        Returns:
            Summary dict with source counts, confidence distribution, unresolved list.
        """
        source_counts = {"static": 0,
                         "deterministic": 0, "ai": 0, "unresolved": 0}
        confidence_scores = []
        unresolved = []

        for mapping in resolved_mappings:
            source_counts[mapping.source] += 1
            if mapping.source != "unresolved":
                confidence_scores.append(mapping.confidence)
            else:
                unresolved.append(mapping.column_name)

        # Compute confidence distribution
        confidence_dist = {
            "high": sum(1 for s in confidence_scores if s >= 0.85),
            "medium": sum(1 for s in confidence_scores if 0.7 <= s < 0.85),
            "low": sum(1 for s in confidence_scores if 0.5 <= s < 0.7),
            "unresolved": len(unresolved)
        }

        return {
            "source_counts": source_counts,
            "confidence_distribution": confidence_dist,
            "unresolved_columns": unresolved,
            "total_resolved": len(resolved_mappings) - len(unresolved),
            "total_columns": len(resolved_mappings)
        }

    def clear_cache(self) -> None:
        """Clear the resolution cache (for testing or fresh resolution)."""
        self._resolved_cache.clear()

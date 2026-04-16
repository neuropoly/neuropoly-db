"""
Fuzzy column header matcher for deterministic phenotype mapping.

Uses rapidfuzz for scoring column headers against known phenotype variable names.
Provides confidence-calibrated matching for exact, fuzzy, and no-match cases.
"""
from typing import Dict, List, Optional, Tuple
from rapidfuzz import fuzz, process


class PhenotypeMatcher:
    """
    Matches column headers to phenotype variables using fuzzy string matching.

    Confidence policy:
    - Exact match (case-insensitive, normalized whitespace): 0.95–1.0
    - Fuzzy token overlap (partial match): 0.75–0.9
    - No match: 0.0
    """

    # Normalization rules for column headers
    NORMALIZATION_MAP = {
        "_": " ",
        "-": " ",
    }

    @staticmethod
    def normalize_header(header: str) -> str:
        """
        Normalize column header for matching.

        Converts to lowercase, replaces underscores/dashes with spaces,
        and strips whitespace.
        """
        normalized = header.lower().strip()
        for old, new in PhenotypeMatcher.NORMALIZATION_MAP.items():
            normalized = normalized.replace(old, new)
        # Collapse multiple spaces
        normalized = " ".join(normalized.split())
        return normalized

    @staticmethod
    def exact_match(header: str, candidates: List[str]) -> Optional[Tuple[str, float]]:
        """
        Check for exact match (case-insensitive, whitespace-normalized).

        Args:
            header: Column header to match.
            candidates: List of known variable names / aliases.

        Returns:
            (matched_candidate, confidence) if exact match found, else None.
        """
        normalized_header = PhenotypeMatcher.normalize_header(header)
        normalized_candidates = {
            PhenotypeMatcher.normalize_header(c): c for c in candidates}

        if normalized_header in normalized_candidates:
            return (normalized_candidates[normalized_header], 1.0)
        return None

    @staticmethod
    def fuzzy_match(
        header: str,
        candidates: List[str],
        score_cutoff: float = 75.0
    ) -> Optional[Tuple[str, float]]:
        """
        Find best fuzzy match using token-based scoring.

        Args:
            header: Column header to match.
            candidates: List of known variable names / aliases.
            score_cutoff: Minimum score (0–100) to consider a match; default 75.

        Returns:
            (best_match, confidence_in_0_to_1) if match found above cutoff, else None.
            Confidence is normalized from [0, 100] to [0.75, 0.9].
        """
        if not candidates:
            return None

        normalized_header = PhenotypeMatcher.normalize_header(header)
        normalized_candidates = [
            PhenotypeMatcher.normalize_header(c) for c in candidates]

        # Use token_set_ratio for partial matches (e.g., "age_at_baseline" vs "age")
        result = process.extractOne(
            normalized_header,
            normalized_candidates,
            scorer=fuzz.token_set_ratio,
            score_cutoff=score_cutoff
        )

        if result is None:
            return None

        best_match, best_score, idx = result

        # Normalize score from [score_cutoff, 100] to [0.75, 0.9]
        # This reserves [0.9, 1.0] for exact matches and [0.5, 0.75) for AI suggestions
        confidence = 0.75 + (best_score - score_cutoff) / \
            (100.0 - score_cutoff) * 0.15
        confidence = min(confidence, 0.9)  # Cap at 0.9

        original_candidate = candidates[idx]
        return (original_candidate, confidence)

    @staticmethod
    def match_header(
        header: str,
        candidates: List[str],
        exact_threshold: float = 1.0,
        fuzzy_threshold: float = 0.75
    ) -> Optional[Tuple[str, float, str]]:
        """
        Match column header with two-tier strategy: exact → fuzzy.

        Args:
            header: Column header to match.
            candidates: List of known variable names / aliases.
            exact_threshold: Confidence threshold for exact match (default 1.0).
            fuzzy_threshold: Confidence threshold for fuzzy match (default 0.75).

        Returns:
            (match, confidence, source) where source is "exact" or "fuzzy", or None.
        """
        # Attempt exact match
        exact_result = PhenotypeMatcher.exact_match(header, candidates)
        if exact_result and exact_result[1] >= exact_threshold:
            return (exact_result[0], exact_result[1], "exact")

        # Attempt fuzzy match
        fuzzy_result = PhenotypeMatcher.fuzzy_match(header, candidates)
        if fuzzy_result and fuzzy_result[1] >= fuzzy_threshold:
            return (fuzzy_result[0], fuzzy_result[1], "fuzzy")

        # No match found
        return None


class ColumnMatcher:
    """
    High-level matcher for mapping dataset column headers to phenotype variables.

    Uses a registry of known mappings and applies fuzzy matching as a fallback.
    """

    def __init__(self, mappings_registry: Dict[str, Dict]):
        """
        Initialize matcher with a registry of known phenotype mappings.

        Args:
            mappings_registry: Dict of column_name → mapping_metadata.
                              Must contain "mappings" key with dict of mappings.
        """
        self.mappings_registry = mappings_registry.get("mappings", {})
        # Build comprehensive list of all known names and aliases
        self.all_known_names = []
        self.name_to_mapping_key = {}

        for key, mapping_data in self.mappings_registry.items():
            self.all_known_names.append(key)
            self.name_to_mapping_key[key] = key

            # Add aliases if present
            if "aliases" in mapping_data:
                for alias in mapping_data["aliases"]:
                    self.all_known_names.append(alias)
                    self.name_to_mapping_key[alias] = key

    def match_column(
        self,
        header: str,
        exact_threshold: float = 1.0,
        fuzzy_threshold: float = 0.75
    ) -> Optional[Tuple[str, float, str]]:
        """
        Match a column header to a known phenotype mapping.

        Args:
            header: Column header to match.
            exact_threshold: Confidence threshold for exact match.
            fuzzy_threshold: Confidence threshold for fuzzy match.

        Returns:
            (mapping_key, confidence, source) or None if no match found.
        """
        match_result = PhenotypeMatcher.match_header(
            header,
            self.all_known_names,
            exact_threshold=exact_threshold,
            fuzzy_threshold=fuzzy_threshold
        )

        if match_result:
            matched_name, confidence, source = match_result
            mapping_key = self.name_to_mapping_key[matched_name]
            return (mapping_key, confidence, source)

        return None

    def get_mapping_data(self, mapping_key: str) -> Optional[Dict]:
        """
        Retrieve full mapping metadata for a matched key.

        Args:
            mapping_key: Key from mappings_registry.

        Returns:
            Mapping metadata dict, or None if key not found.
        """
        return self.mappings_registry.get(mapping_key)

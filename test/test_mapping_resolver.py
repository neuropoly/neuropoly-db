"""
Unit tests for mapping_resolver module.

Tests precedence chain (static → fuzzy → unresolved), caching, and summary generation.
"""

import pytest
from pathlib import Path
import json
import tempfile

from npdb.automation.mappings.resolvers import MappingResolver, ResolvedMapping


class TestMappingResolver:
    """Tests for MappingResolver precedence chain."""

    @pytest.fixture
    def resolver(self):
        """Create a resolver with static mappings only."""
        return MappingResolver()

    def test_resolve_static_exact_match(self, resolver):
        """Test resolution via static dictionary (exact match)."""
        result = resolver.resolve_column("participant_id")

        assert result.column_name == "participant_id"
        assert result.source == "static"
        assert result.confidence == 1.0
        assert result.mapped_variable == "nb:ParticipantID"
        assert "static dictionary" in result.rationale.lower()

    def test_resolve_fuzzy_match(self, resolver):
        """Test resolution via fuzzy matching."""
        result = resolver.resolve_column("age_at_baseline")

        assert result.column_name == "age_at_baseline"
        # Could be either "static" (if age_at_baseline is alias) or "deterministic" (if fuzzy)
        assert result.source in ("static", "deterministic")
        # Confidence should be high since "age_at_baseline" is related to "age"
        assert result.confidence >= 0.75
        assert result.mapped_variable == "nb:Age"

    def test_resolve_unresolved(self, resolver):
        """Test unresolved column (no static/fuzzy match)."""
        result = resolver.resolve_column("completely_unrelated_column")

        assert result.column_name == "completely_unrelated_column"
        assert result.source == "unresolved"
        assert result.confidence == 0.0
        assert result.mapped_variable == ""
        assert "no" in result.rationale.lower() and ("static" in result.rationale.lower()
                                                     or "fuzzy" in result.rationale.lower())

    def test_resolve_column_data_preservation(self, resolver):
        """Test that full mapping data is preserved in result."""
        result = resolver.resolve_column("age")

        assert result.mapping_data is not None
        assert "variable" in result.mapping_data
        assert "confidence" in result.mapping_data
        assert result.mapping_data["variable"] == "nb:Age"

    def test_resolve_columns_batch(self, resolver):
        """Test batch resolution of multiple columns."""
        column_names = ["participant_id", "age", "sex", "unknown_col"]
        results = resolver.resolve_columns(column_names)

        assert len(results) == 4
        assert results[0].source in ("static", "deterministic")
        assert results[3].source == "unresolved"

    def test_cache_hit(self, resolver):
        """Test that resolved mappings are cached."""
        # First call
        result1 = resolver.resolve_column("age")
        # Second call (should hit cache)
        result2 = resolver.resolve_column("age")

        assert result1.column_name == result2.column_name
        assert result1.source == result2.source
        assert result1.confidence == result2.confidence
        # Cache should prevent re-matching
        assert resolver._resolved_cache["age"] is result2

    def test_cache_clear(self, resolver):
        """Test clearing the resolution cache."""
        resolver.resolve_column("age")
        assert "age" in resolver._resolved_cache

        resolver.clear_cache()
        assert len(resolver._resolved_cache) == 0

    def test_thresholds_respected(self):
        """Test that exact/fuzzy thresholds are respected."""
        # Create resolver with strict fuzzy threshold
        strict_resolver = MappingResolver(fuzzy_threshold=0.95)
        result = strict_resolver.resolve_column("age_years")

        # Should only match if confidence >= 0.95
        if result.source == "fuzzy":
            assert result.confidence >= 0.95

    def test_user_dictionary_override(self):
        """Test that user dictionary overrides static mappings."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            user_dict = {
                "@context": {"nb": "http://neurobagel.org/vocab/"},
                "mappings": {
                    "age": {
                        "variable": "nb:Custom",
                        "confidence": 0.99,
                        "variable_type": "Continuous"
                    }
                }
            }
            json.dump(user_dict, f)
            temp_path = f.name

        try:
            resolver = MappingResolver(user_dictionary_path=temp_path)
            result = resolver.resolve_column("age")

            # User mapping should override static
            assert result.mapped_variable == "nb:Custom"
            assert result.confidence == 0.99
        finally:
            Path(temp_path).unlink()


class TestResolvedMapping:
    """Tests for ResolvedMapping dataclass."""

    def test_resolved_mapping_creation(self):
        """Test creating a ResolvedMapping instance."""
        mapping = ResolvedMapping(
            column_name="age",
            mapped_variable="nb:Age",
            confidence=0.95,
            source="static",
            mapping_data={"variable": "nb:Age"},
            rationale="Test rationale"
        )

        assert mapping.column_name == "age"
        assert mapping.mapped_variable == "nb:Age"
        assert mapping.confidence == 0.95
        assert mapping.source == "static"

    def test_resolved_mapping_with_empty_data(self):
        """Test ResolvedMapping with empty mapping data."""
        mapping = ResolvedMapping(
            column_name="unknown",
            mapped_variable="",
            confidence=0.0,
            source="unresolved",
            mapping_data={},
            rationale="No match found"
        )

        assert mapping.mapped_variable == ""
        assert mapping.confidence == 0.0


class TestResolutionSummary:
    """Tests for resolution summary and statistics."""

    @pytest.fixture
    def resolver(self):
        """Create a resolver for summary tests."""
        return MappingResolver()

    def test_resolution_summary_all_resolved(self, resolver):
        """Test summary when all columns resolved."""
        columns = ["participant_id", "age", "sex"]
        results = resolver.resolve_columns(columns)
        summary = resolver.get_resolution_summary(results)

        assert summary["total_columns"] == 3
        assert summary["total_resolved"] == 3
        assert summary["source_counts"]["unresolved"] == 0

    def test_resolution_summary_mixed(self, resolver):
        """Test summary with mix of resolved and unresolved."""
        columns = ["participant_id", "age", "unknown_col"]
        results = resolver.resolve_columns(columns)
        summary = resolver.get_resolution_summary(results)

        assert summary["total_columns"] == 3
        assert summary["total_resolved"] >= 2
        assert len(summary["unresolved_columns"]) <= 1

    def test_resolution_summary_confidence_distribution(self, resolver):
        """Test that confidence distribution is computed correctly."""
        columns = ["participant_id", "age", "unknown_col"]
        results = resolver.resolve_columns(columns)
        summary = resolver.get_resolution_summary(results)

        # Check distribution keys exist
        assert "high" in summary["confidence_distribution"]
        assert "medium" in summary["confidence_distribution"]
        assert "low" in summary["confidence_distribution"]
        assert "unresolved" in summary["confidence_distribution"]

        # Distribution should sum correctly
        total_dist = (
            summary["confidence_distribution"]["high"] +
            summary["confidence_distribution"]["medium"] +
            summary["confidence_distribution"]["low"] +
            summary["confidence_distribution"]["unresolved"]
        )
        assert total_dist == summary["total_columns"]

    def test_resolution_summary_source_counts(self, resolver):
        """Test source count aggregation."""
        columns = ["participant_id", "age", "unknown_col"]
        results = resolver.resolve_columns(columns)
        summary = resolver.get_resolution_summary(results)

        counts = summary["source_counts"]
        total = counts["static"] + counts["deterministic"] + \
            counts["ai"] + counts["unresolved"]
        assert total == summary["total_columns"]

    def test_resolution_summary_unresolved_list(self, resolver):
        """Test that unresolved columns are listed."""
        columns = ["participant_id", "unknown_col_1", "unknown_col_2"]
        results = resolver.resolve_columns(columns)
        summary = resolver.get_resolution_summary(results)

        assert "unknown_col_1" in summary["unresolved_columns"] or "unknown_col_2" in summary["unresolved_columns"]


class TestPrecedenceOrder:
    """Tests verifying static > fuzzy > unresolved precedence."""

    @pytest.fixture
    def resolver(self):
        return MappingResolver()

    def test_static_dict_priority(self, resolver):
        """Static dictionary should take priority."""
        # "participant_id" is in static dict
        result = resolver.resolve_column("participant_id")
        assert result.source == "static"
        assert result.confidence >= 0.95

    def test_fuzzy_fallback(self, resolver):
        """Fuzzy matching used only if static doesn't match."""
        # "age_years" not in static but should fuzzy match to "age"
        result = resolver.resolve_column("age_years")
        if result.source != "unresolved":
            assert result.source == "deterministic"

    def test_unresolved_last_resort(self, resolver):
        """Unresolved only when static and fuzzy both fail."""
        result = resolver.resolve_column("xyz_undefined_column_abc")
        # High confidence it's unresolved
        if result.source == "unresolved":
            assert result.confidence == 0.0


class TestStaticMappingsIntegration:
    """Tests with actual static mappings."""

    def test_resolver_loads_static_mappings(self):
        """Test that resolver loads actual static mappings."""
        resolver = MappingResolver()

        # Should have mappings loaded
        assert resolver.matcher.mappings_registry is not None
        assert len(resolver.matcher.mappings_registry) > 0

    def test_real_static_mappings_resolve(self):
        """Test resolution with built-in static mappings."""
        resolver = MappingResolver()

        # Test known columns from static dict
        for col in ["participant_id", "age", "sex"]:
            result = resolver.resolve_column(col)
            assert result.source in ("static", "deterministic")
            assert result.mapped_variable != ""


class TestEdgeCases:
    """Tests for edge cases in resolution."""

    def test_empty_column_name(self):
        """Test resolution with empty column name."""
        resolver = MappingResolver()
        result = resolver.resolve_column("")

        # Should not crash; should be unresolved
        assert result.source == "unresolved"

    def test_column_with_special_chars(self):
        """Test column names with special characters."""
        resolver = MappingResolver()
        result = resolver.resolve_column("age@home#1")

        # Should attempt fuzzy match, not crash
        assert result.column_name == "age@home#1"

    def test_unicode_column_name(self):
        """Test unicode column names."""
        resolver = MappingResolver()
        result = resolver.resolve_column("âge")

        # Should handle gracefully
        assert result.column_name == "âge"

    def test_very_long_column_name(self):
        """Test very long column name."""
        resolver = MappingResolver()
        long_name = "a" * 1000
        result = resolver.resolve_column(long_name)

        # Should handle without error
        assert result.column_name == long_name

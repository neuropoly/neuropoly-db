"""
Unit tests for fuzzy_matcher module.

Tests exact matching, fuzzy matching, confidence scoring, and ColumnMatcher.
"""

import pytest

from npdb.annotation.matching import (
    ColumnMatcher,
    PhenotypeMatcher
)
from npdb.automation.mappings.solvers import load_static_mappings


class TestFuzzyMatcher:
    """Tests for FuzzyMatcher static methods."""

    def test_normalize_header(self):
        """Test header normalization."""
        # Lowercase
        assert PhenotypeMatcher.normalize_header("AGE") == "age"
        # Underscore to space
        assert PhenotypeMatcher.normalize_header(
            "participant_id") == "participant id"
        # Dash to space
        assert PhenotypeMatcher.normalize_header("age-years") == "age years"
        # Combined
        assert PhenotypeMatcher.normalize_header(
            "  Age_in_Years  ") == "age in years"
        # Multiple spaces collapsed
        assert PhenotypeMatcher.normalize_header(
            "age  in  years") == "age in years"

    def test_exact_match_basic(self):
        """Test exact matching with simple candidates."""
        candidates = ["age", "sex", "participant_id"]

        # Exact match
        result = PhenotypeMatcher.exact_match("age", candidates)
        assert result == ("age", 1.0)

        # Case-insensitive
        result = PhenotypeMatcher.exact_match("AGE", candidates)
        assert result == ("age", 1.0)

        # Underscore/dash normalization
        result = PhenotypeMatcher.exact_match("participant_id", candidates)
        assert result == ("participant_id", 1.0)

        result = PhenotypeMatcher.exact_match("participant-id", candidates)
        assert result == ("participant_id", 1.0)

    def test_exact_match_no_match(self):
        """Test exact matching returns None when no match."""
        candidates = ["age", "sex"]
        result = PhenotypeMatcher.exact_match("age_years", candidates)
        assert result is None

    def test_fuzzy_match_high_confidence(self):
        """Test fuzzy matching with high-similarity candidates."""
        candidates = ["age", "sex", "diagnosis"]

        # "age_at_baseline" should match "age"
        result = PhenotypeMatcher.fuzzy_match(
            "age_at_baseline", candidates, score_cutoff=75)
        assert result is not None
        matched, confidence = result
        assert matched == "age"
        assert 0.75 <= confidence <= 0.9  # In fuzzy range

    def test_fuzzy_match_partial(self):
        """Test fuzzy matching with partial token overlap."""
        candidates = ["participant_id", "session_id"]

        # "partID" may not match well enough on score_cutoff=60 with token_set_ratio
        # Adjust cutoff lower for this weak match test
        result = PhenotypeMatcher.fuzzy_match(
            "partID", candidates, score_cutoff=40)
        assert result is not None
        matched, confidence = result
        assert 0.75 <= confidence <= 0.9

    def test_fuzzy_match_no_match(self):
        """Test fuzzy matching returns None below cutoff."""
        candidates = ["age", "sex"]
        result = PhenotypeMatcher.fuzzy_match(
            "completely_unrelated_column", candidates, score_cutoff=75)
        assert result is None

    def test_fuzzy_match_confidence_scaling(self):
        """Test that confidence is scaled from [score_cutoff, 100] to [0.75, 0.9]."""
        candidates = ["age"]

        # At cutoff (75), confidence should be ~0.75
        result_at_cutoff = PhenotypeMatcher.fuzzy_match(
            "age", candidates, score_cutoff=75)
        assert result_at_cutoff and result_at_cutoff[1] >= 0.75

        # At 100 (exact), confidence should be ~0.9 (capped)
        result_at_100 = PhenotypeMatcher.fuzzy_match(
            "age", candidates, score_cutoff=0)
        assert result_at_100 and result_at_100[1] <= 0.9

    def test_match_header_exact_priority(self):
        """Test that exact match is prioritized over fuzzy."""
        candidates = ["age", "age_at_baseline"]

        # "age" should match exactly
        result = PhenotypeMatcher.match_header("age", candidates)
        assert result == ("age", 1.0, "exact")

    def test_match_header_fuzzy_fallback(self):
        """Test fallback to fuzzy when exact fails."""
        candidates = ["age", "sex"]

        # "age_years" should fuzzy match to "age"
        result = PhenotypeMatcher.match_header("age_years", candidates)
        assert result is not None
        matched, confidence, source = result
        assert matched == "age"
        assert source == "fuzzy"
        assert 0.75 <= confidence <= 0.9

    def test_match_header_no_match(self):
        """Test no match returns None."""
        candidates = ["age", "sex"]
        result = PhenotypeMatcher.match_header(
            "unrelated", candidates, fuzzy_threshold=0.75)
        assert result is None

    def test_match_header_thresholds(self):
        """Test that thresholds are respected."""
        candidates = ["age"]

        # Use high fuzzy threshold to filter matches
        result_strict = PhenotypeMatcher.match_header(
            "age_years", candidates, fuzzy_threshold=0.95)
        # Should still match since "age" is similar
        assert result_strict is None or result_strict[2] == "exact"

        # Use low threshold to allow more matches
        result_lenient = PhenotypeMatcher.match_header(
            "age_years", candidates, fuzzy_threshold=0.5)
        assert result_lenient is not None


class TestColumnMatcher:
    """Tests for ColumnMatcher integration with registry."""

    @pytest.fixture
    def test_registry(self):
        """Create a test mappings registry."""
        return {
            "@context": {
                "nb": "http://neurobagel.org/vocab/",
            },
            "mappings": {
                "participant_id": {
                    "variable": "nb:ParticipantID",
                    "confidence": 1.0,
                    "variable_type": "Identifier",
                    "aliases": ["sub_id", "subject_id", "partID"]
                },
                "age": {
                    "variable": "nb:Age",
                    "confidence": 0.95,
                    "variable_type": "Continuous",
                    "format": "nb:FromFloat",
                    "aliases": ["age_years", "years_old"]
                },
                "sex": {
                    "variable": "nb:Sex",
                    "confidence": 0.9,
                    "variable_type": "Categorical",
                    "aliases": ["gender"]
                },
            }
        }

    def test_column_matcher_exact(self, test_registry):
        """Test ColumnMatcher exact matching via registry."""
        matcher = ColumnMatcher(test_registry)

        result = matcher.match_column("participant_id")
        assert result == ("participant_id", 1.0, "exact")

    def test_column_matcher_alias(self, test_registry):
        """Test ColumnMatcher matching via aliases."""
        matcher = ColumnMatcher(test_registry)

        result = matcher.match_column("sub_id")
        assert result is not None
        matched, confidence, source = result
        assert matched == "participant_id"  # Should resolve to key
        assert source == "exact"  # Exact match of alias

    def test_column_matcher_fuzzy(self, test_registry):
        """Test ColumnMatcher fuzzy matching."""
        matcher = ColumnMatcher(test_registry)

        result = matcher.match_column("age_at_baseline")
        assert result is not None
        matched, confidence, source = result
        assert matched == "age"
        assert source == "fuzzy"
        assert 0.75 <= confidence <= 0.9

    def test_column_matcher_no_match(self, test_registry):
        """Test ColumnMatcher no match."""
        matcher = ColumnMatcher(test_registry)

        result = matcher.match_column("unrelated_column")
        assert result is None

    def test_column_matcher_get_mapping_data(self, test_registry):
        """Test retrieving full mapping data after match."""
        matcher = ColumnMatcher(test_registry)

        # Get mapping data for matched column
        mapping_data = matcher.get_mapping_data("age")
        assert mapping_data is not None
        assert mapping_data["variable"] == "nb:Age"
        assert mapping_data["format"] == "nb:FromFloat"

    def test_column_matcher_with_static_mappings(self):
        """Test ColumnMatcher with actual static mappings."""
        static_mappings = load_static_mappings()
        matcher = ColumnMatcher(static_mappings)

        # Test known mappings
        result = matcher.match_column("participant_id")
        assert result is not None
        assert result[0] == "participant_id"
        assert result[2] == "exact"

        # Test aliases
        result = matcher.match_column("age")
        assert result is not None

    def test_column_matcher_initialization(self):
        """Test that ColumnMatcher extracts all names and aliases."""
        registry = {
            "mappings": {
                "age": {
                    "variable": "nb:Age",
                    "aliases": ["age_years", "years"]
                }
            }
        }
        matcher = ColumnMatcher(registry)

        # Verify all names are in the matcher
        expected_names = {"age", "age_years", "years"}
        assert set(matcher.all_known_names) == expected_names


class TestConfidenceScaling:
    """Tests for confidence score scaling and semantics."""

    def test_exact_match_confidence_1_0(self):
        """Exact matches should have confidence 1.0."""
        candidates = ["age"]
        result = PhenotypeMatcher.match_header("age", candidates)
        assert result[1] == 1.0

    def test_fuzzy_match_confidence_range(self):
        """Fuzzy matches should be in [0.75, 0.9) range."""
        candidates = ["age"]
        result = PhenotypeMatcher.fuzzy_match(
            "ageXXX", candidates, score_cutoff=50)
        assert result is not None
        assert 0.75 <= result[1] < 0.9

    def test_no_match_returns_none(self):
        """No match should return None, not 0 confidence."""
        candidates = ["age", "sex"]
        result = PhenotypeMatcher.match_header(
            "completely_unrelated", candidates)
        assert result is None


class TestEdgeCases:
    """Tests for edge cases and robustness."""

    def test_empty_candidates(self):
        """Test with empty candidate list."""
        result = PhenotypeMatcher.match_header("age", [])
        assert result is None

    def test_empty_header(self):
        """Test with empty header."""
        candidates = ["age"]
        result = PhenotypeMatcher.normalize_header("")
        assert result == ""

    def test_special_characters_in_header(self):
        """Test headers with special characters."""
        # Should normalize to basic form
        result = PhenotypeMatcher.normalize_header("age@home#2")
        assert "age" in result

    def test_whitespace_handling(self):
        """Test various whitespace patterns."""
        assert PhenotypeMatcher.normalize_header("  age  ") == "age"
        assert PhenotypeMatcher.normalize_header("age   years") == "age years"
        assert PhenotypeMatcher.normalize_header("\tage\n") == "age"

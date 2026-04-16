import pytest

from npdb.automation.mappings.solvers import (
    load_static_mappings,
    load_user_mappings,
    merge_mappings,
)


@pytest.fixture
def builtin_mappings():
    """Load built-in static mappings."""
    return load_static_mappings()


@pytest.fixture
def user_mappings_path(tmp_path):
    """Create a temporary user mappings file."""
    import json
    user_mapping = {
        "@context": {
            "nb": "http://neurobagel.org/vocab/",
        },
        "mappings": {
            "custom_age": {
                "variable": "nb:Age",
                "format": "nb:FromFloat",
                "confidence": 0.9,
                "variableType": "Continuous"
            }
        }
    }
    mapping_file = tmp_path / "user_mappings.json"
    with open(mapping_file, "w") as f:
        json.dump(user_mapping, f)
    return mapping_file


class TestMappingsRegistry:
    """Tests for static phenotype mappings."""

    def test_load_builtin_mappings(self, builtin_mappings):
        """Test loading built-in static mappings."""
        assert builtin_mappings is not None
        assert "@context" in builtin_mappings
        assert "mappings" in builtin_mappings
        assert "participant_id" in builtin_mappings["mappings"]

    def test_builtin_mappings_context(self, builtin_mappings):
        """Test that @context has expected vocabulary prefixes."""
        context = builtin_mappings["@context"]
        assert "nb" in context
        assert "snomed" in context
        assert "ncit" in context

    def test_participant_id_mapping(self, builtin_mappings):
        """Test participant_id mapping has correct structure."""
        mapping = builtin_mappings["mappings"]["participant_id"]
        assert mapping["variable"] == "nb:ParticipantID"
        assert mapping["confidence"] == 1.0
        assert mapping["variableType"] == "Identifier"

    def test_age_mapping_has_format(self, builtin_mappings):
        """Test age mapping includes format field."""
        mapping = builtin_mappings["mappings"]["age"]
        assert "format" in mapping
        assert mapping["format"] == "nb:FromFloat"

    def test_sex_mapping_has_levels(self, builtin_mappings):
        """Test sex mapping includes level mappings."""
        mapping = builtin_mappings["mappings"]["sex"]
        assert "levels" in mapping
        assert "M" in mapping["levels"]
        assert "F" in mapping["levels"]

    def test_merge_mappings_respects_user_priority(self, builtin_mappings, user_mappings_path):
        """Test that user mappings take precedence over built-in."""
        user_map = load_user_mappings(user_mappings_path)
        merged = merge_mappings(builtin_mappings, user_map)

        # Custom age should be present
        assert "custom_age" in merged["mappings"]
        # Built-in mappings should still be present
        assert "participant_id" in merged["mappings"]

"""
Unit tests for ui_interaction module.

Tests annotation data building and form filler actions.
"""

import pytest
from unittest.mock import AsyncMock

from npdb.automation.mappings.resolvers import ResolvedMapping
from npdb.automation.playwright.resolver import (
    AnnotationUIBuilder,
    ColumnAnnotationData,
    FormatAnnotationData,
    FormFillerActions,
    ValueAnnotationData
)


class TestColumnAnnotationData:
    """Tests for ColumnAnnotationData dataclass."""

    def test_creation(self):
        """Test creating ColumnAnnotationData."""
        data = ColumnAnnotationData(
            column_name="age",
            variable="nb:Age",
            variable_type="Continuous",
            format="nb:FromFloat",
            confidence=0.95
        )

        assert data.column_name == "age"
        assert data.variable == "nb:Age"
        assert data.variable_type == "Continuous"
        assert data.confidence == 0.95

    def test_defaults(self):
        """Test default values."""
        data = ColumnAnnotationData(column_name="test")

        assert data.column_name == "test"
        assert data.description == ""
        assert data.variable == ""
        assert data.variable_type == ""
        assert data.confidence == 0.0


class TestValueAnnotationData:
    """Tests for ValueAnnotationData dataclass."""

    def test_categorical_value(self):
        """Test categorical value mapping."""
        data = ValueAnnotationData(
            column_index=1,
            raw_value="M",
            mapped_term="snomed:248153007",
            mapped_label="Male"
        )

        assert data.column_index == 1
        assert data.raw_value == "M"
        assert data.mapped_term == "snomed:248153007"
        assert data.is_missing_value is False

    def test_missing_value(self):
        """Test missing value marker."""
        data = ValueAnnotationData(
            column_index=0,
            raw_value="NA",
            is_missing_value=True
        )

        assert data.is_missing_value is True
        assert data.raw_value == "NA"


class TestFormatAnnotationData:
    """Tests for FormatAnnotationData dataclass."""

    def test_continuous_format(self):
        """Test continuous variable format."""
        data = FormatAnnotationData(
            column_index=0,
            format="nb:FromFloat",
            units="years",
            missing_values=["NA", "N/A"]
        )

        assert data.column_index == 0
        assert data.format == "nb:FromFloat"
        assert data.units == "years"
        assert data.missing_values == ["NA", "N/A"]

    def test_format_defaults(self):
        """Test format defaults."""
        data = FormatAnnotationData(column_index=0)

        assert data.format == "nb:FromFloat"
        assert data.units == ""
        assert data.missing_values is None


class TestAnnotationUIBuilder:
    """Tests for AnnotationUIBuilder."""

    def test_build_column_annotation_from_resolved(self):
        """Test building column annotation from resolver result."""
        resolved = ResolvedMapping(
            column_name="age",
            mapped_variable="nb:Age",
            confidence=0.95,
            source="static",
            mapping_data={
                "variable_type": "Continuous",
                "format": "nb:FromFloat"
            },
            rationale="Static dict match"
        )

        annotation = AnnotationUIBuilder.build_column_annotation(
            "age",
            resolved
        )

        assert annotation.column_name == "age"
        assert annotation.variable == "nb:Age"
        assert annotation.confidence == 0.95
        assert annotation.variable_type == "Continuous"

    def test_build_column_annotation_infer_categorical(self):
        """Test inferring categorical type from unique values."""
        resolved = ResolvedMapping(
            column_name="sex",
            mapped_variable="nb:Sex",
            confidence=0.9,
            source="static",
            mapping_data={"variable_type": ""},
            rationale=""
        )

        annotation = AnnotationUIBuilder.build_column_annotation(
            "sex",
            resolved,
            unique_values=["M", "F", "O"]
        )

        assert annotation.variable_type == "Categorical"

    def test_build_column_annotation_infer_continuous(self):
        """Test inferring continuous type from many unique values."""
        resolved = ResolvedMapping(
            column_name="score",
            mapped_variable="nb:Assessment",
            confidence=0.5,
            source="unresolved",
            mapping_data={"variable_type": ""},
            rationale=""
        )

        many_values = [str(i) for i in range(100)]
        annotation = AnnotationUIBuilder.build_column_annotation(
            "score",
            resolved,
            unique_values=many_values
        )

        assert annotation.variable_type == "Continuous"

    def test_build_value_annotations_with_levels(self):
        """Test building value annotations with level mappings."""
        mapping_data = {
            "levels": {
                "M": {"termURL": "snomed:248153007", "label": "Male"},
                "F": {"termURL": "snomed:248152002", "label": "Female"}
            }
        }

        values = AnnotationUIBuilder.build_value_annotations(
            column_index=1,
            unique_values=["M", "F"],
            mapping_data=mapping_data
        )

        assert len(values) == 2
        assert values[0].raw_value == "M"
        assert values[0].mapped_label == "Male"
        assert values[1].raw_value == "F"
        assert values[1].mapped_label == "Female"

    def test_build_value_annotations_no_mapping(self):
        """Test building value annotations without level mappings."""
        mapping_data = {"levels": {}}

        values = AnnotationUIBuilder.build_value_annotations(
            column_index=0,
            unique_values=["A", "B"],
            mapping_data=mapping_data
        )

        assert len(values) == 2
        # Should default to raw value as label
        assert values[0].mapped_label == "A"
        assert values[1].mapped_label == "B"

    def test_build_format_annotation(self):
        """Test building format annotation."""
        mapping_data = {
            "format": "nb:FromBounded",
            "missing_values": ["NA", "-999"]
        }

        format_ann = AnnotationUIBuilder.build_format_annotation(
            column_index=0,
            mapping_data=mapping_data
        )

        assert format_ann.column_index == 0
        assert format_ann.format == "nb:FromBounded"
        assert "NA" in format_ann.missing_values

    def test_build_format_annotation_defaults(self):
        """Test format annotation defaults."""
        mapping_data = {}

        format_ann = AnnotationUIBuilder.build_format_annotation(
            column_index=2,
            mapping_data=mapping_data
        )

        assert format_ann.column_index == 2
        assert format_ann.format == "nb:FromFloat"  # Default
        assert format_ann.units == ""


class TestFormFillerActions:
    """Tests for FormFillerActions form filling."""

    @pytest.mark.asyncio
    async def test_fill_column_annotation(self):
        """Test fill_column_annotation calls correct browser methods."""
        # Mock BrowserSession
        mock_session = AsyncMock()
        mock_session.fill = AsyncMock()
        mock_session.select_option = AsyncMock()

        data = ColumnAnnotationData(
            column_name="age",
            description="Age in years",
            variable="nb:Age",
            variable_type="Continuous"
        )

        await FormFillerActions.fill_column_annotation(mock_session, data)

        # Verify fill() was called for description
        mock_session.fill.assert_called_once()
        call_args = mock_session.fill.call_args
        # selector contains "description"
        assert "description" in call_args[0][0]
        assert call_args[0][1] == "Age in years"  # correct value passed

        # Verify select_option() was called twice (variable + type)
        assert mock_session.select_option.call_count == 2
        calls = mock_session.select_option.call_args_list
        # First call: variable select
        assert "variable" in calls[0][0][0].lower()
        assert calls[0][0][1] == "nb:Age"
        # Second call: type select
        assert "type" in calls[1][0][0].lower()
        assert calls[1][0][1] == "Continuous"

    @pytest.mark.asyncio
    async def test_fill_value_annotations(self):
        """Test fill_value_annotations iterates and fills correctly."""
        mock_session = AsyncMock()
        mock_session.fill = AsyncMock()
        mock_session.select_option = AsyncMock()

        values = [
            ValueAnnotationData(
                column_index=0,
                raw_value="M",
                mapped_term="snomed:248153007"
            ),
            ValueAnnotationData(
                column_index=0,
                raw_value="F",
                mapped_term="snomed:248152002"
            )
        ]

        await FormFillerActions.fill_value_annotations(mock_session, values)

        # Verify fill() called twice for raw values
        assert mock_session.fill.call_count == 2
        # Verify select_option() called twice for mapped terms
        assert mock_session.select_option.call_count == 2

        # Check first value
        fill_calls = mock_session.fill.call_args_list
        assert fill_calls[0][0][1] == "M"
        assert fill_calls[1][0][1] == "F"

        # Check mapped terms
        select_calls = mock_session.select_option.call_args_list
        assert select_calls[0][0][1] == "snomed:248153007"
        assert select_calls[1][0][1] == "snomed:248152002"

    @pytest.mark.asyncio
    async def test_fill_format_annotation_with_units(self):
        """Test fill_format_annotation with units."""
        mock_session = AsyncMock()
        mock_session.select_option = AsyncMock()
        mock_session.fill = AsyncMock()

        fmt = FormatAnnotationData(
            column_index=0,
            format="nb:FromFloat",
            units="years"
        )

        await FormFillerActions.fill_format_annotation(mock_session, fmt)

        # Verify format select
        mock_session.select_option.assert_called_once()
        select_call = mock_session.select_option.call_args
        assert "format" in select_call[0][0].lower()
        assert select_call[0][1] == "nb:FromFloat"

        # Verify units fill
        mock_session.fill.assert_called_once()
        fill_call = mock_session.fill.call_args
        assert "units" in fill_call[0][0].lower()
        assert fill_call[0][1] == "years"

    @pytest.mark.asyncio
    async def test_fill_format_annotation_no_units(self):
        """Test fill_format_annotation skips units when empty."""
        mock_session = AsyncMock()
        mock_session.select_option = AsyncMock()
        mock_session.fill = AsyncMock()

        fmt = FormatAnnotationData(
            column_index=0,
            format="nb:FromBounded",
            units=""  # Empty units
        )

        await FormFillerActions.fill_format_annotation(mock_session, fmt)

        # Verify format select called
        mock_session.select_option.assert_called_once()

        # Verify fill NOT called (units conditional skipped)
        mock_session.fill.assert_not_called()


class TestEdgeCases:
    """Tests for edge cases."""

    def test_build_value_annotations_empty_list(self):
        """Test with empty unique values."""
        values = AnnotationUIBuilder.build_value_annotations(
            column_index=0,
            unique_values=[],
            mapping_data={}
        )

        assert values == []

    def test_build_value_annotations_large_list(self):
        """Test with many unique values."""
        many_values = [f"val_{i}" for i in range(1000)]
        values = AnnotationUIBuilder.build_value_annotations(
            column_index=0,
            unique_values=many_values,
            mapping_data={}
        )

        assert len(values) == 1000

    def test_column_annotation_missing_mapping_data(self):
        """Test with minimal mapping data."""
        resolved = ResolvedMapping(
            column_name="x",
            mapped_variable="nb:Unknown",
            confidence=0.0,
            source="unresolved",
            mapping_data={},  # Empty
            rationale=""
        )

        annotation = AnnotationUIBuilder.build_column_annotation(
            "x",
            resolved
        )

        assert annotation.column_name == "x"
        assert annotation.format == ""

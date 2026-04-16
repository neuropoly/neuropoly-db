"""
Unit tests for annotation_steps module.

Tests step detection, navigation, and UI pattern selectors.
"""

from npdb.annotation.automation import (
    AnnotationStep,
    AnnotationUIPatterns,
    StepInfo,
    StepNavigator
)


class TestAnnotationStep:
    """Tests for AnnotationStep enum."""

    def test_step_enum_values(self):
        """Test that all steps are defined."""
        steps = [
            AnnotationStep.UPLOAD,
            AnnotationStep.COLUMN_ANNOTATION,
            AnnotationStep.VALUE_ANNOTATION,
            AnnotationStep.EXPORT,
            AnnotationStep.UNKNOWN
        ]
        assert len(steps) == 5

    def test_step_enum_names(self):
        """Test enum names."""
        assert AnnotationStep.UPLOAD.value == "upload"
        assert AnnotationStep.COLUMN_ANNOTATION.value == "column_annotation"
        assert AnnotationStep.VALUE_ANNOTATION.value == "value_annotation"
        assert AnnotationStep.EXPORT.value == "export"
        assert AnnotationStep.UNKNOWN.value == "unknown"


class TestStepInfo:
    """Tests for StepInfo dataclass."""

    def test_step_info_creation(self):
        """Test creating a StepInfo."""
        info = StepInfo(
            step=AnnotationStep.UPLOAD,
            title="Upload",
            description="Upload TSV",
            next_button_selector="button:next",
            content_area_selector="div:upload"
        )

        assert info.step == AnnotationStep.UPLOAD
        assert info.title == "Upload"
        assert info.next_button_selector == "button:next"

    def test_step_navigator_config_upload(self):
        """Test step config for upload step."""
        upload_info = StepNavigator.STEPS_CONFIG[AnnotationStep.UPLOAD]

        assert upload_info.step == AnnotationStep.UPLOAD
        assert upload_info.title == "Upload Data"
        assert "TSV" in upload_info.description.upper()
        assert upload_info.next_button_selector is not None


class TestStepNavigator:
    """Tests for StepNavigator utilities."""

    def test_get_step_info_all_steps(self):
        """Test getting step info for all steps."""
        for step in [
            AnnotationStep.UPLOAD,
            AnnotationStep.COLUMN_ANNOTATION,
            AnnotationStep.VALUE_ANNOTATION,
            AnnotationStep.EXPORT
        ]:
            info = StepNavigator.get_step_info(step)
            assert info is not None
            assert info.step == step

    def test_get_step_info_unknown(self):
        """Test getting step info for unknown step."""
        info = StepNavigator.get_step_info(AnnotationStep.UNKNOWN)
        assert info is None

    def test_get_steps_in_order(self):
        """Test steps are returned in correct sequence."""
        steps = StepNavigator.get_steps_in_order()

        expected = [
            AnnotationStep.UPLOAD,
            AnnotationStep.COLUMN_ANNOTATION,
            AnnotationStep.VALUE_ANNOTATION,
            AnnotationStep.EXPORT,
        ]

        assert steps == expected
        assert len(steps) == 4

    def test_step_config_completeness(self):
        """Test that all steps have complete config."""
        for step in StepNavigator.get_steps_in_order():
            info = StepNavigator.get_step_info(step)

            assert info.title is not None
            assert len(info.title) > 0
            assert info.next_button_selector is not None
            assert info.content_area_selector is not None

    def test_fallback_selectors_coverage(self):
        """Test fallback selectors exist for all steps."""
        for step in StepNavigator.get_steps_in_order():
            assert step in StepNavigator.FALLBACK_SELECTORS
            assert StepNavigator.FALLBACK_SELECTORS[step] is not None


class TestAnnotationUIPatterns:
    """Tests for AnnotationUIPatterns selectors."""

    def test_column_card_selector(self):
        """Test column card selector."""
        assert AnnotationUIPatterns.COLUMN_CARD_SELECTOR is not None
        assert "column" in AnnotationUIPatterns.COLUMN_CARD_SELECTOR.lower()

    def test_value_mapping_selector(self):
        """Test value mapping selector."""
        assert AnnotationUIPatterns.VALUE_MAPPING_SELECTOR is not None
        assert "value" in AnnotationUIPatterns.VALUE_MAPPING_SELECTOR.lower()

    def test_download_button_selector(self):
        """Test download button selector."""
        assert "Download" in AnnotationUIPatterns.DOWNLOAD_BUTTON

    def test_get_column_row(self):
        """Test column row selector generation."""
        row0 = AnnotationUIPatterns.get_column_row(0)
        row1 = AnnotationUIPatterns.get_column_row(1)

        assert row0 != row1
        assert "0" in row0 or "1" in row0
        assert COLUMN_CARD_SELECTOR in row0

    def test_get_column_row_numbering(self):
        """Test column row numbering is 1-indexed for nth-of-type."""
        row0 = AnnotationUIPatterns.get_column_row(0)  # First column
        assert ":nth-of-type(1)" in row0

        row3 = AnnotationUIPatterns.get_column_row(3)  # Fourth column
        assert ":nth-of-type(4)" in row3

    def test_get_value_mapping_row(self):
        """Test value mapping row selector."""
        row = AnnotationUIPatterns.get_value_mapping_row(0, 0)

        assert "data-column='0'" in row
        assert "data-value='0'" in row

    def test_get_value_mapping_row_multiple(self):
        """Test value mapping row selectors differ by column/value."""
        row00 = AnnotationUIPatterns.get_value_mapping_row(0, 0)
        row01 = AnnotationUIPatterns.get_value_mapping_row(0, 1)
        row10 = AnnotationUIPatterns.get_value_mapping_row(1, 0)

        assert row00 != row01
        assert row00 != row10


COLUMN_CARD_SELECTOR = AnnotationUIPatterns.COLUMN_CARD_SELECTOR


class TestUIPatternCompleteness:
    """Tests for coverage of all UI elements."""

    def test_all_form_selectors_defined(self):
        """Test that common form selectors are defined."""
        selectors = [
            AnnotationUIPatterns.COLUMN_DESCRIPTION_INPUT,
            AnnotationUIPatterns.COLUMN_VARIABLE_SELECT,
            AnnotationUIPatterns.COLUMN_TYPE_SELECT,
            AnnotationUIPatterns.FORMAT_SELECT,
            AnnotationUIPatterns.DOWNLOAD_BUTTON,
        ]

        for selector in selectors:
            assert selector is not None
            assert len(selector) > 0

    def test_input_vs_select_selectors(self):
        """Test input and select selectors are distinct."""
        # Input selectors should have 'input' tag
        assert "input" in AnnotationUIPatterns.COLUMN_DESCRIPTION_INPUT.lower()
        assert "input" in AnnotationUIPatterns.VALUE_INPUT.lower()

        # Select selectors should have 'select' tag
        assert "select" in AnnotationUIPatterns.COLUMN_VARIABLE_SELECT.lower()
        assert "select" in AnnotationUIPatterns.FORMAT_SELECT.lower()


class TestEdgeCases:
    """Tests for edge cases."""

    def test_large_column_index(self):
        """Test with large column index."""
        row = AnnotationUIPatterns.get_column_row(999)
        assert ":nth-of-type(1000)" in row

    def test_large_value_index(self):
        """Test with large value index."""
        row = AnnotationUIPatterns.get_value_mapping_row(10, 100)
        assert "data-column='10'" in row
        assert "data-value='100'" in row

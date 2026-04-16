"""
Annotation tool step management and navigation.

Detects current step in Neurobagel annotation workflow and routes to handlers.
Steps: upload → column annotation → value annotation → export.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class AnnotationStep(Enum):
    """Annotation workflow steps."""
    UPLOAD = "upload"
    COLUMN_ANNOTATION = "column_annotation"
    VALUE_ANNOTATION = "value_annotation"
    EXPORT = "export"
    UNKNOWN = "unknown"


@dataclass
class StepInfo:
    """Information about current annotation step."""
    step: AnnotationStep
    title: str
    description: str
    next_button_selector: str
    content_area_selector: str


class StepNavigator:
    """
    Detects and navigates through annotation tool steps.

    Provides step detection, next/prev navigation, and validation.
    """

    # Step detection selectors and metadata
    STEPS_CONFIG = {
        AnnotationStep.UPLOAD: StepInfo(
            step=AnnotationStep.UPLOAD,
            title="Upload Data",
            description="Upload TSV file and optional phenotype dictionary",
            next_button_selector="button:has-text('Next')",
            content_area_selector="[data-testid='upload-step']"
        ),
        AnnotationStep.COLUMN_ANNOTATION: StepInfo(
            step=AnnotationStep.COLUMN_ANNOTATION,
            title="Annotate Columns",
            description="Describe columns and map to standardized variables",
            next_button_selector="button:has-text('Next')",
            content_area_selector="[data-testid='column-step']"
        ),
        AnnotationStep.VALUE_ANNOTATION: StepInfo(
            step=AnnotationStep.VALUE_ANNOTATION,
            title="Annotate Values",
            description="Map categorical values and define continuous formats",
            next_button_selector="button:has-text('Finish')",
            content_area_selector="[data-testid='value-step']"
        ),
        AnnotationStep.EXPORT: StepInfo(
            step=AnnotationStep.EXPORT,
            title="Export Results",
            description="Download phenotypes.json and phenotypes_annotations.json",
            next_button_selector="button:has-text('Download')",
            content_area_selector="[data-testid='export-step']"
        ),
    }

    # Fallback selectors if data-testid not available
    FALLBACK_SELECTORS = {
        AnnotationStep.UPLOAD: "input[type='file']",
        AnnotationStep.COLUMN_ANNOTATION: "div:has-text('Describe columns')",
        AnnotationStep.VALUE_ANNOTATION: "div:has-text('Map values')",
        AnnotationStep.EXPORT: "button:has-text('Download')",
    }

    @staticmethod
    def get_step_info(step: AnnotationStep) -> Optional[StepInfo]:
        """
        Get metadata for a given step.

        Args:
            step: AnnotationStep enum.

        Returns:
            StepInfo with selectors and description, or None if unknown.
        """
        return StepNavigator.STEPS_CONFIG.get(step)

    @staticmethod
    def get_steps_in_order() -> list[AnnotationStep]:
        """
        Get steps in workflow order.

        Returns:
            List of AnnotationStep in sequence.
        """
        return [
            AnnotationStep.UPLOAD,
            AnnotationStep.COLUMN_ANNOTATION,
            AnnotationStep.VALUE_ANNOTATION,
            AnnotationStep.EXPORT,
        ]


class AnnotationUIPatterns:
    """
    Common UI patterns for annotation tool interaction.

    Provides reusable selectors and helpers for common forms.
    """

    # Column annotation card/row selectors
    COLUMN_CARD_SELECTOR = "[data-testid='column-card']"
    COLUMN_DESCRIPTION_INPUT = "input[placeholder='Column description']"
    COLUMN_VARIABLE_SELECT = "select[name='variable']"
    COLUMN_TYPE_SELECT = "select[name='variable_type']"
    COLUMN_ASSESSMENT_SELECT = "select[name='assessment_tool']"

    # Value annotation selectors
    VALUE_MAPPING_SELECTOR = "[data-testid='value-mapping']"
    VALUE_INPUT = "input[placeholder='Raw value']"
    VALUE_TERM_SELECT = "select[name='mapped_term']"
    VALUE_STANDARD_LABEL = "input[placeholder='Standard term label']"
    MISSING_VALUE_CHECKBOX = "input[name='is_missing_value']"

    # Format selectors (for continuous variables)
    FORMAT_SELECT = "select[name='format']"
    UNITS_INPUT = "input[placeholder='Units (optional)']"
    MISSING_VALUES_INPUT = "textarea[placeholder='Missing value indicators (comma-separated)']"

    # Export selectors
    DOWNLOAD_BUTTON = "button:has-text('Download')"
    EXPORT_FILENAME = "[data-testid='export-filename']"

    @staticmethod
    def get_column_row(column_index: int) -> str:
        """
        Get selector for column row by index.

        Args:
            column_index: Zero-based column index.

        Returns:
            Selector for column row.
        """
        return f"{AnnotationUIPatterns.COLUMN_CARD_SELECTOR}:nth-of-type({column_index + 1})"

    @staticmethod
    def get_value_mapping_row(column_index: int, value_index: int) -> str:
        """
        Get selector for value mapping row.

        Args:
            column_index: Zero-based column index.
            value_index: Zero-based value index within column.

        Returns:
            Selector for value mapping row.
        """
        return f"{AnnotationUIPatterns.VALUE_MAPPING_SELECTOR}[data-column='{column_index}'][data-value='{value_index}']"

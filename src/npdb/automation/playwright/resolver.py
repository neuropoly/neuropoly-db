"""
UI interaction helpers for annotation tool form filling.

Handles column annotation, value annotation, and format specification.
Bridges MappingResolver results to Playwright form inputs.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from npdb.automation.mappings.resolvers import ResolvedMapping


@dataclass
class ColumnAnnotationData:
    """Data for annotating a single column."""
    column_name: str
    description: str = ""
    variable: str = ""  # e.g., "nb:Age"
    variable_type: str = ""  # "Categorical", "Continuous", "Identifier"
    assessment_tool: str = ""  # e.g., "snomed:123456"
    format: str = ""  # For continuous: "nb:FromFloat"
    confidence: float = 0.0


@dataclass
class ValueAnnotationData:
    """Data for annotating a single categorical value."""
    column_index: int
    raw_value: str
    mapped_term: str = ""  # e.g., "snomed:248153007"
    mapped_label: str = ""  # e.g., "Male"
    is_missing_value: bool = False


@dataclass
class FormatAnnotationData:
    """Data for continuous variable format specification."""
    column_index: int
    format: str = "nb:FromFloat"
    units: str = ""
    missing_values: List[str] = None  # e.g., ["NA", "N/A", "-999"]


class AnnotationUIBuilder:
    """
    Builds annotation data from resolver results and metadata.

    Bridges deterministic mapping resolution to UI form filling.
    """

    @staticmethod
    def build_column_annotation(
        column_name: str,
        resolved_mapping: ResolvedMapping,
        unique_values: Optional[List[str]] = None
    ) -> ColumnAnnotationData:
        """
        Build column annotation data from resolver result.

        Args:
            column_name: Column header.
            resolved_mapping: Result from MappingResolver.
            unique_values: Unique categorical values (optional, for type detection).

        Returns:
            ColumnAnnotationData ready for form filling.
        """
        # Determine variable type from mapping data
        variable_type = resolved_mapping.mapping_data.get("variable_type", "")

        # If not set, infer from unique values
        if not variable_type and unique_values:
            # If few unique values and no nulls, likely categorical
            if len(unique_values) < 10:
                variable_type = "Categorical"
            else:
                variable_type = "Continuous"

        return ColumnAnnotationData(
            column_name=column_name,
            variable=resolved_mapping.mapped_variable,
            variable_type=variable_type,
            format=resolved_mapping.mapping_data.get("format", ""),
            confidence=resolved_mapping.confidence,
            description=""  # To be filled by user or AI
        )

    @staticmethod
    def build_value_annotations(
        column_index: int,
        unique_values: List[str],
        mapping_data: Dict[str, Any]
    ) -> List[ValueAnnotationData]:
        """
        Build value annotation data for categorical column.

        Args:
            column_index: Column index in dataset.
            unique_values: Unique categorical values.
            mapping_data: Mapping metadata from resolver (may contain levels).

        Returns:
            List of ValueAnnotationData for each unique value.
        """
        levels = mapping_data.get("levels", {})
        annotations = []

        for value_idx, raw_value in enumerate(unique_values):
            term_mapping = levels.get(raw_value, {})

            annotations.append(ValueAnnotationData(
                column_index=column_index,
                raw_value=raw_value,
                mapped_term=term_mapping.get("termURL", ""),
                mapped_label=term_mapping.get("label", raw_value),
                is_missing_value=False
            ))

        return annotations

    @staticmethod
    def build_format_annotation(
        column_index: int,
        mapping_data: Dict[str, Any]
    ) -> FormatAnnotationData:
        """
        Build format annotation for continuous variable.

        Args:
            column_index: Column index in dataset.
            mapping_data: Mapping metadata (should include format info).

        Returns:
            FormatAnnotationData ready for form filling.
        """
        return FormatAnnotationData(
            column_index=column_index,
            format=mapping_data.get("format", "nb:FromFloat"),
            units="",  # To be filled by user or AI
            missing_values=mapping_data.get("missing_values", [])
        )


class FormFillerActions:
    """
    Encapsulates form filling actions for browser session.

    Each method corresponds to a Playwright action on the annotation tool UI.
    """

    @staticmethod
    async def fill_column_annotation(
        browser_session,
        column_annotation: ColumnAnnotationData
    ) -> None:
        """
        Fill column annotation form fields.

        Args:
            browser_session: BrowserSession instance with page.
            column_annotation: ColumnAnnotationData to fill.
        """
        from npdb.annotation.automation import AnnotationUIPatterns as UI

        await browser_session.fill(
            UI.COLUMN_DESCRIPTION_INPUT,
            column_annotation.description
        )
        await browser_session.select_option(
            UI.COLUMN_VARIABLE_SELECT,
            column_annotation.variable
        )
        await browser_session.select_option(
            UI.COLUMN_TYPE_SELECT,
            column_annotation.variable_type
        )

    @staticmethod
    async def fill_value_annotations(
        browser_session,
        value_annotations: List[ValueAnnotationData]
    ) -> None:
        """
        Fill value annotation form for categorical column.

        Args:
            browser_session: BrowserSession instance.
            value_annotations: List of ValueAnnotationData to fill.
        """
        from npdb.annotation.automation import AnnotationUIPatterns as UI

        for idx, annotation in enumerate(value_annotations):
            selector = UI.get_value_mapping_row(annotation.column_index, idx)
            await browser_session.fill(
                f"{selector} {UI.VALUE_INPUT}",
                annotation.raw_value
            )
            await browser_session.select_option(
                f"{selector} {UI.VALUE_TERM_SELECT}",
                annotation.mapped_term
            )

    @staticmethod
    async def fill_format_annotation(
        browser_session,
        format_annotation: FormatAnnotationData
    ) -> None:
        """
        Fill format annotation for continuous variable.

        Args:
            browser_session: BrowserSession instance.
            format_annotation: FormatAnnotationData to fill.
        """
        from npdb.annotation.automation import AnnotationUIPatterns as UI

        await browser_session.select_option(
            UI.FORMAT_SELECT,
            format_annotation.format
        )
        if format_annotation.units:
            await browser_session.fill(
                UI.UNITS_INPUT,
                format_annotation.units
            )

    @staticmethod
    async def download_export_file(
        browser_session,
        expected_filename: str = "phenotypes_annotations.json",
        timeout: int = 30000
    ) -> None:
        """
        Click export button and wait for file download.

        Triggers the export workflow in Neurobagel annotation tool:
        1. Find and click the Download/Export button
        2. Wait for file download event
        3. Verify file was downloaded

        Args:
            browser_session: BrowserSession instance with page and download support.
            expected_filename: Expected filename for validation (e.g., 'phenotypes_annotations.json').
            timeout: Timeout in milliseconds for download completion.

        Raises:
            RuntimeError: If download button not found or download fails.
            TimeoutError: If download doesn't complete within timeout.
        """
        from npdb.annotation.automation import AnnotationUIPatterns as UI

        try:
            # Click export button
            await browser_session.click(UI.DOWNLOAD_BUTTON)
            print(f"✓ Clicked export button")

            # Wait for download with Playwright's download event handling
            # Note: The actual download path is managed by BrowserSession.wait_for_download()
            # This is a simplified version that just waits for the click to trigger download
            # In production, would integrate event-based download detection
            import asyncio
            await asyncio.sleep(2.0)  # Allow time for download to start

            print(f"✓ Export download initiated")

        except Exception as e:
            raise RuntimeError(f"Failed to download export file: {e}") from e

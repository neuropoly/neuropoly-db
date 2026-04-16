"""
Locator discovery and validation for annotation tool UI elements.

Provides utilities to find, validate, and manage Playwright selectors.
Handles fallbacks when primary selectors fail.
"""


from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class LocatorInfo:
    """Metadata for a UI element locator."""
    name: str
    primary_selector: str
    fallback_selectors: List[str]
    required: bool = True
    description: str = ""


class LocatorRegistry:
    """
    Registry of known locators for annotation tool.

    Maintains primary and fallback selectors for all UI elements.
    Provides validation and selection strategies.
    """

    # File upload input
    FILE_INPUT = LocatorInfo(
        name="file_input",
        primary_selector="input[type='file'][accept='.tsv,.csv']",
        fallback_selectors=[
            "input[type='file']",
            "[data-testid='file-upload-input']",
            "label:contains('Upload')~input[type='file']"
        ],
        description="File upload input for participants.tsv"
    )

    # Column annotation fields
    COLUMN_CARDS = LocatorInfo(
        name="column_cards",
        primary_selector="[data-testid='column-card']",
        fallback_selectors=[
            ".column-annotation-card",
            "[role='region']:has(h3)",
            "div.annotation-column"
        ],
        description="Container for column annotation form"
    )

    COLUMN_DESCRIPTION = LocatorInfo(
        name="column_description",
        primary_selector="textarea[name='description']",
        fallback_selectors=[
            "input[placeholder*='Description']",
            "[data-testid='column-description']"
        ],
        description="Column description textarea"
    )

    COLUMN_VARIABLE = LocatorInfo(
        name="column_variable",
        primary_selector="select[name='variable']",
        fallback_selectors=[
            "[data-testid='variable-select']",
            "select:nth-of-type(1)"
        ],
        description="Standardized variable selection dropdown"
    )

    COLUMN_TYPE = LocatorInfo(
        name="column_type",
        primary_selector="select[name='variable_type']",
        fallback_selectors=[
            "[data-testid='type-select']",
            "select:nth-of-type(2)"
        ],
        description="Variable type selection (Categorical/Continuous)"
    )

    # Value annotation fields
    VALUE_ROWS = LocatorInfo(
        name="value_rows",
        primary_selector="[data-testid='value-mapping-row']",
        fallback_selectors=[
            ".value-mapping-row",
            "div:has(input[name='raw_value'])"
        ],
        description="Row container for value mapping"
    )

    VALUE_INPUT = LocatorInfo(
        name="value_input",
        primary_selector="input[name='raw_value']",
        fallback_selectors=[
            "input[placeholder*='Value']",
            "[data-testid='raw-value-input']"
        ],
        description="Raw value input field"
    )

    VALUE_TERM = LocatorInfo(
        name="value_term",
        primary_selector="select[name='mapped_term']",
        fallback_selectors=[
            "[data-testid='term-select']",
            "select:has(option)"
        ],
        description="Mapped term selection"
    )

    # Format fields (for continuous)
    FORMAT_SELECT = LocatorInfo(
        name="format_select",
        primary_selector="select[name='format']",
        fallback_selectors=[
            "[data-testid='format-select']",
            "select:contains('FromFloat')"
        ],
        description="Format selection for continuous variables"
    )

    UNITS_INPUT = LocatorInfo(
        name="units_input",
        primary_selector="input[name='units']",
        fallback_selectors=[
            "input[placeholder*='Units']",
            "[data-testid='units-input']"
        ],
        description="Units input field"
    )

    # Navigation buttons
    NEXT_BUTTON = LocatorInfo(
        name="next_button",
        primary_selector="button:has-text('Next')",
        fallback_selectors=[
            "button[aria-label='Next']",
            "[data-testid='next-button']",
            "button:contains('Next Step')"
        ],
        description="Next button for step navigation"
    )

    FINISH_BUTTON = LocatorInfo(
        name="finish_button",
        primary_selector="button:has-text('Finish')",
        fallback_selectors=[
            "button[aria-label='Finish']",
            "[data-testid='finish-button']"
        ],
        description="Finish button on last step"
    )

    DOWNLOAD_BUTTON = LocatorInfo(
        name="download_button",
        primary_selector="button:has-text('Download')",
        fallback_selectors=[
            "button[aria-label='Download']",
            "[data-testid='download-button']",
            "a:contains('Download')"
        ],
        description="Download results button"
    )

    # All known locators
    ALL_LOCATORS = [
        FILE_INPUT,
        COLUMN_CARDS,
        COLUMN_DESCRIPTION,
        COLUMN_VARIABLE,
        COLUMN_TYPE,
        VALUE_ROWS,
        VALUE_INPUT,
        VALUE_TERM,
        FORMAT_SELECT,
        UNITS_INPUT,
        NEXT_BUTTON,
        FINISH_BUTTON,
        DOWNLOAD_BUTTON,
    ]

    @staticmethod
    def get_locator(name: str) -> Optional[LocatorInfo]:
        """
        Get locator by name.

        Args:
            name: Locator name.

        Returns:
            LocatorInfo or None if not found.
        """
        for loc in LocatorRegistry.ALL_LOCATORS:
            if loc.name == name:
                return loc
        return None

    @staticmethod
    def get_all_locators() -> List[LocatorInfo]:
        """Get all registered locators."""
        return LocatorRegistry.ALL_LOCATORS

    @staticmethod
    def get_required_locators() -> List[LocatorInfo]:
        """Get locators marked as required."""
        return [loc for loc in LocatorRegistry.ALL_LOCATORS if loc.required]


class LocatorValidator:
    """
    Validates locators against page state.

    Checks selector validity and provides recommendations.
    """

    @staticmethod
    async def validate_locator(page, locator_info: LocatorInfo) -> bool:
        """
        Validate that a locator exists on the page.

        Args:
            page: Playwright page object.
            locator_info: LocatorInfo to validate.

        Returns:
            True if primary selector found, False otherwise.
        """
        try:
            element = page.locator(locator_info.primary_selector)
            return await element.count() > 0
        except Exception:
            return False

    @staticmethod
    async def find_working_selector(
        page,
        locator_info: LocatorInfo
    ) -> Optional[str]:
        """
        Find first working selector from primary + fallbacks.

        Args:
            page: Playwright page object.
            locator_info: LocatorInfo with selectors to try.

        Returns:
            First working selector string, or None if all fail.
        """
        selectors_to_try = [locator_info.primary_selector] + \
            locator_info.fallback_selectors

        for selector in selectors_to_try:
            try:
                element = page.locator(selector)
                if await element.count() > 0:
                    return selector
            except Exception:
                continue

        return None

    @staticmethod
    async def validate_step(page, step_name: str) -> Dict[str, bool]:
        """
        Validate all required locators for a step exist.

        Args:
            page: Playwright page object.
            step_name: Step identifier (upload, column, value, export).

        Returns:
            Dict of locator_name -> found (bool).
        """
        # Map step names to locators
        step_locators = {
            "upload": [LocatorRegistry.FILE_INPUT, LocatorRegistry.NEXT_BUTTON],
            "column": [LocatorRegistry.COLUMN_CARDS, LocatorRegistry.NEXT_BUTTON],
            "value": [LocatorRegistry.VALUE_ROWS, LocatorRegistry.FINISH_BUTTON],
            "export": [LocatorRegistry.DOWNLOAD_BUTTON],
        }

        locators = step_locators.get(step_name, [])
        results = {}

        for loc in locators:
            results[loc.name] = await LocatorValidator.validate_locator(page, loc)

        return results


class LocatorBuilder:
    """
    Builds dynamic selectors for parametric elements.

    Handles varying column/row indices and complex queries.
    """

    @staticmethod
    def column_row_selector(column_index: int) -> str:
        """
        Build selector for column at given index.

        Args:
            column_index: Zero-based column index.

        Returns:
            Selector string.
        """
        # Use nth-of-type or data attribute if available
        return f"[data-testid='column-card'][data-column='{column_index}']"

    @staticmethod
    def value_row_selector(column_index: int, value_index: int) -> str:
        """
        Build selector for value row at given column/value indices.

        Args:
            column_index: Zero-based column index.
            value_index: Zero-based value index.

        Returns:
            Selector string.
        """
        return f"[data-testid='value-mapping-row'][data-column='{column_index}'][data-value='{value_index}']"

    @staticmethod
    def field_in_row(row_selector: str, field_name: str) -> str:
        """
        Build selector for field within a row.

        Args:
            row_selector: Row selector.
            field_name: Field name (input, select, textarea).

        Returns:
            Selector for field within row.
        """
        return f"{row_selector} [name='{field_name}']"

    @staticmethod
    def input_by_placeholder(placeholder: str) -> str:
        """
        Build selector for input by placeholder text.

        Args:
            placeholder: Placeholder text (partial match).

        Returns:
            Selector for input.
        """
        return f"input[placeholder*='{placeholder}']"

    @staticmethod
    def button_by_text(text: str) -> str:
        """
        Build selector for button by text.

        Args:
            text: Button text (exact or partial).

        Returns:
            Selector for button.
        """
        return f"button:has-text('{text}')"


class LocatorCache:
    """
    Caches validated selectors to avoid repeated validation.

    Stores primary selectors that worked for reuse.
    """

    def __init__(self):
        """Initialize empty cache."""
        self._cache: Dict[str, str] = {}

    def get(self, locator_name: str) -> Optional[str]:
        """
        Get cached selector for locator.

        Args:
            locator_name: Name of locator.

        Returns:
            Cached selector string, or None.
        """
        return self._cache.get(locator_name)

    def set(self, locator_name: str, selector: str) -> None:
        """
        Cache a working selector.

        Args:
            locator_name: Name of locator.
            selector: Working selector string.
        """
        self._cache[locator_name] = selector

    def clear(self) -> None:
        """Clear the cache."""
        self._cache.clear()

    def size(self) -> int:
        """Get cache size."""
        return len(self._cache)

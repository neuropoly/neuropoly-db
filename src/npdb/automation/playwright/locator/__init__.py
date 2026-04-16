"""
Locator inspection and discovery for annotation tool UI elements.

Provides runtime inspection of page DOM to discover and validate selectors.
Helps diagnose selector mismatches and find alternative selectors when primary fails.
"""
from playwright.async_api import Page

import json
from typing import Any, Dict, List


class LocatorInspector:
    """
    Runtime inspector for discovering and debugging UI element selectors.

    Provides methods to:
    - List all elements matching a selector pattern
    - Find elements by text, role, or attributes
    - Dump page structure for debugging
    - Suggest fallback selectors based on what exists on page
    """

    def __init__(self, page: Page):
        """
        Initialize inspector for a Playwright page.

        Args:
            page: Playwright Page object to inspect
        """
        self.page = page

    async def find_all_inputs(self) -> List[Dict[str, Any]]:
        """
        Find all input elements on page and describe them.

        Returns:
            List of dictionaries describing each input element
        """
        inputs = []

        # Get all input elements
        all_inputs = await self.page.query_selector_all("input")

        for i, inp in enumerate(all_inputs):
            try:
                input_data = {
                    "index": i,
                    "type": await inp.get_attribute("type") or "text",
                    "name": await inp.get_attribute("name"),
                    "id": await inp.get_attribute("id"),
                    "class": await inp.get_attribute("class"),
                    "data_testid": await inp.get_attribute("data-testid"),
                    "accept": await inp.get_attribute("accept"),
                    "placeholder": await inp.get_attribute("placeholder"),
                    "visible": await inp.is_visible(),
                    "enabled": await inp.is_enabled(),
                }
                inputs.append(input_data)
            except Exception as e:
                print(f"Error inspecting input {i}: {e}")

        return inputs

    async def find_file_inputs(self) -> List[Dict[str, Any]]:
        """
        Find all file input elements specifically.

        Returns:
            List of file input element descriptions
        """
        file_inputs = []
        all_inputs = await self.find_all_inputs()

        for inp in all_inputs:
            if inp.get("type") == "file":
                file_inputs.append(inp)

        return file_inputs

    async def find_upload_buttons(self) -> List[Dict[str, Any]]:
        """
        Find upload-related buttons (containing "upload" text).

        Returns:
            List of button element descriptions
        """
        buttons = []

        try:
            all_buttons = await self.page.query_selector_all("button")

            for i, btn in enumerate(all_buttons):
                try:
                    text = await btn.inner_text()
                    if "upload" in text.lower():
                        btn_data = {
                            "index": i,
                            "text": text,
                            "id": await btn.get_attribute("id"),
                            "class": await btn.get_attribute("class"),
                            "data_testid": await btn.get_attribute("data-testid"),
                            "visible": await btn.is_visible(),
                            "enabled": await btn.is_enabled(),
                        }
                        buttons.append(btn_data)
                except:
                    pass
        except:
            pass

        return buttons

    async def find_by_text(self, text: str, selector: str = "input, button, label") -> List[Dict[str, Any]]:
        """
        Find elements by containing text.

        Args:
            text: Text to search for
            selector: CSS selector to search within (default: input, button, label)

        Returns:
            List of matching element descriptions
        """
        results = []

        try:
            # Use Playwright's locator with has_text
            locators = self.page.locator(f"{selector}:has-text('{text}')")
            count = await locators.count()

            for i in range(count):
                try:
                    loc = locators.nth(i)
                    elem_data = {
                        "text": await loc.inner_text(),
                        "tag": await loc.evaluate("el => el.tagName"),
                        "id": await loc.get_attribute("id"),
                        "class": await loc.get_attribute("class"),
                        "data_testid": await loc.get_attribute("data-testid"),
                    }
                    results.append(elem_data)
                except:
                    pass
        except:
            pass

        return results

    async def find_form_elements(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Find common form elements (inputs, textareas, selects).

        Returns:
            Dictionary grouping elements by type
        """
        elements = {
            "inputs": [],
            "textareas": [],
            "selects": [],
            "buttons": [],
        }

        # Inputs
        try:
            inputs = await self.page.query_selector_all("input")
            for inp in inputs[:5]:  # Limit to first 5
                try:
                    elements["inputs"].append({
                        "type": await inp.get_attribute("type"),
                        "name": await inp.get_attribute("name"),
                        "visible": await inp.is_visible(),
                    })
                except:
                    pass
        except:
            pass

        # Textareas
        try:
            textareas = await self.page.query_selector_all("textarea")
            for ta in textareas[:5]:
                try:
                    elements["textareas"].append({
                        "name": await ta.get_attribute("name"),
                        "visible": await ta.is_visible(),
                    })
                except:
                    pass
        except:
            pass

        # Selects
        try:
            selects = await self.page.query_selector_all("select")
            for sel in selects[:5]:
                try:
                    elements["selects"].append({
                        "name": await sel.get_attribute("name"),
                        "visible": await sel.is_visible(),
                    })
                except:
                    pass
        except:
            pass

        # Buttons
        try:
            buttons = await self.page.query_selector_all("button")
            for btn in buttons[:5]:
                try:
                    elements["buttons"].append({
                        "text": await btn.inner_text() or "[EMPTY]",
                        "visible": await btn.is_visible(),
                    })
                except:
                    pass
        except:
            pass

        return elements

    async def test_selector(self, selector: str) -> Dict[str, Any]:
        """
        Test if a selector matches any elements on the page.

        Args:
            selector: CSS/XPath selector to test

        Returns:
            Dictionary with test results
        """
        try:
            locator = self.page.locator(selector)
            count = await locator.count()

            if count == 0:
                return {
                    "selector": selector,
                    "found": False,
                    "count": 0,
                    "message": "No elements found"
                }

            first = locator.first
            visible = await first.is_visible() if count > 0 else False
            enabled = await first.is_enabled() if count > 0 else False

            return {
                "selector": selector,
                "found": True,
                "count": count,
                "visible": visible,
                "enabled": enabled,
            }
        except Exception as e:
            return {
                "selector": selector,
                "found": False,
                "error": str(e)
            }

    async def print_page_structure(self) -> str:
        """
        Generate a debug string showing page structure for file uploads.

        Returns:
            Formatted string showing page elements
        """
        output = []
        output.append("=" * 80)
        output.append("FILE INPUT ELEMENTS FOUND ON PAGE")
        output.append("=" * 80)

        file_inputs = await self.find_file_inputs()
        if file_inputs:
            for i, inp in enumerate(file_inputs):
                output.append(f"\nFile Input #{i+1}:")
                for key, val in inp.items():
                    output.append(f"  {key}: {val}")
        else:
            output.append("\n⚠ NO FILE INPUT ELEMENTS FOUND!")

        output.append("\n" + "=" * 80)
        output.append("UPLOAD-RELATED BUTTONS FOUND ON PAGE")
        output.append("=" * 80)

        upload_buttons = await self.find_upload_buttons()
        if upload_buttons:
            for i, btn in enumerate(upload_buttons):
                output.append(f"\nUpload Button #{i+1}:")
                for key, val in btn.items():
                    output.append(f"  {key}: {val}")
        else:
            output.append("\n(No upload buttons found)")

        output.append("\n" + "=" * 80)
        output.append("ALL FORM ELEMENTS")
        output.append("=" * 80)

        forms = await self.find_form_elements()
        output.append(json.dumps(forms, indent=2))

        return "\n".join(output)


async def diagnose_upload_selector(page: Page) -> str:
    """
    Run comprehensive diagnosis of upload selector on current page.

    Args:
        page: Playwright Page object

    Returns:
        Diagnostic report string
    """
    inspector = LocatorInspector(page)

    report = []
    report.append("\n🔍 SELECTOR DIAGNOSIS REPORT")
    report.append("=" * 80)

    # Test common selectors
    common_selectors = [
        "input[type='file']",
        "input[type='file'][accept='.tsv,.csv']",
        "[data-testid='file-upload-input']",
        "[data-testid='file-input']",
        "input[id*='upload']",
        "input[name*='file']",
        "input[accept*='tsv']",
        "label:has(input[type='file'])",
        ".upload-input",
        "#file-input",
    ]

    report.append("\n📋 TESTING COMMON SELECTORS:")
    for selector in common_selectors:
        result = await inspector.test_selector(selector)
        status = "✓" if result.get("found") else "✗"
        report.append(f"\n  {status} {selector}")
        if result.get("found"):
            report.append(
                f"     Count: {result['count']}, Visible: {result['visible']}, Enabled: {result['enabled']}")
        else:
            if result.get("error"):
                report.append(f"     Error: {result['error']}")

    # Full page structure
    report.append("\n\n" + await inspector.print_page_structure())

    return "\n".join(report)

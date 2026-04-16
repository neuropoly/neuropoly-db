"""
Playwright browser session management for annotation automation.

Handles lifecycle: launch, navigate, upload files, wait for states, cleanup.
Supports headless and headed modes with configurable timeouts.
Includes manual retry logic for transient failures and artifact capture on errors.
"""

import asyncio
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


class NBAnnotationToolBrowserSession:
    """
    Manages Playwright browser lifecycle for Neurobagel annotation tool.

    Handles:
    - Browser launch (headless/headed modes)
    - Page navigation to annotate.neurobagel.org
    - File uploads (TSV + optional dictionary JSON)
    - Timeout and error handling
    - Graceful cleanup
    """

    ANNOTATION_URL = "https://annotate.neurobagel.org"

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 300,
        artifacts_dir: Optional[Path] = None
    ):
        """
        Initialize browser session configuration.

        Args:
            headless: Run in headless mode (no UI window).
            timeout: Global timeout for page actions (seconds).
            artifacts_dir: Directory for screenshots/traces on failure. Will be created if provided.

        Raises:
            OSError: If artifacts_dir cannot be created.
        """
        self.headless = headless
        self.timeout = timeout * 1000  # Convert to ms for Playwright
        self.artifacts_dir = artifacts_dir
        self.trace_path = None

        # Session state
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None

        # Create artifacts directory if specified
        if self.artifacts_dir:
            try:
                self.artifacts_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise OSError(
                    f"Failed to create artifacts directory '{self.artifacts_dir}': {e}") from e

    async def launch(self) -> None:
        """
        Launch Playwright browser and create page.

        Raises:
            RuntimeError: If browser launch fails.
        """
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless
            )

            # Configure context with tracing for artifacts
            context_kwargs = {}
            if self.artifacts_dir:
                self.trace_path = self.artifacts_dir / "trace.zip"
                context_kwargs["record_video_dir"] = str(self.artifacts_dir)

            self.context = await self.browser.new_context()

            # Start tracing if artifacts dir specified
            if self.artifacts_dir:
                await self.context.tracing.start(screenshots=True, snapshots=True)

            self.page = await self.context.new_page()
            self.page.set_default_timeout(self.timeout)

        except Exception as e:
            await self.cleanup()
            raise RuntimeError(f"Failed to launch browser: {e}") from e

    async def navigate_to(self, url: str = ANNOTATION_URL) -> None:
        """
        Navigate to annotation tool or specified URL.

        Uses 'load' state instead of 'networkidle' to avoid hanging on slow networks.

        Args:
            url: URL to navigate to (default: annotate.neurobagel.org).

        Raises:
            RuntimeError: If navigation fails.
        """
        if not self.page:
            raise RuntimeError("Browser not launched. Call launch() first.")

        try:
            # Use 'load' instead of 'networkidle' to avoid hanging
            # 30 second timeout
            await self.page.goto(url, wait_until="load", timeout=30000)
        except Exception as e:
            raise RuntimeError(f"Failed to navigate to {url}: {e}") from e

    async def click_get_started(self) -> None:
        """
        Click the 'Get Started' button on landing page to proceed to upload form.

        This is the initial step after landing on the annotation tool.
        Tries multiple selector variations to find the button.

        Raises:
            RuntimeError: If button not found or click fails.
        """
        if not self.page:
            raise RuntimeError("Browser not launched. Call launch() first.")

        # Try multiple selectors for "Get Started" button
        selectors = [
            "button:has-text('Get Started')",
            "button:has-text('Get started')",
            "[data-testid='get-started-button']",
            "button:contains('Get Started')",
            "button[aria-label='Get Started']",
            "button.get-started",
            "//button[contains(text(), 'Get Started')]",
        ]

        last_error = None
        for selector in selectors:
            try:
                button = self.page.locator(selector)
                # Check if button exists and is visible
                if await button.count() > 0:
                    # 5s to become visible
                    await button.first.wait_for(timeout=5000)
                    await button.first.click(timeout=5000)
                    print(
                        f"✓ Clicked 'Get Started' button with selector: {selector}")
                    await asyncio.sleep(1.0)  # Brief wait for page transition
                    return
            except Exception as e:
                last_error = e
                continue

        # If we get here, button was not found
        raise RuntimeError(
            f"Could not find or click 'Get Started' button. Tried {len(selectors)} selectors. "
            f"Last error: {last_error}"
        )

    async def _find_file_input_selector(self) -> Optional[str]:
        """
        Auto-discover file input selector using fallback strategies.

        Tries common file upload selectors in order of likelihood.
        Returns first successful selector found.

        Returns:
            Working selector string, or None if no file input found
        """
        fallback_selectors = [
            "input[type='file']",
            "input[type='file'][accept*='tsv']",
            "input[type='file'][accept*='csv']",
            "[data-testid='file-upload-input']",
            "[data-testid='file-input']",
            "input[id*='upload']",
            "input[name*='file']",
            ".file-input",
            "#file-input",
        ]

        for selector in fallback_selectors:
            try:
                locator = self.page.locator(selector)
                # Try to find element with short timeout
                if await locator.count() > 0:
                    # Check if it's visible/accessible
                    first = locator.first
                    try:
                        await first.wait_for(timeout=1000)  # Quick check
                        print(f"✓ Auto-discovered file input: {selector}")
                        return selector
                    except:
                        # Found but not visible, may appear later
                        print(f"◐ Found input hiding at: {selector}")
                        return selector
            except:
                continue

        return None

    async def _diagnose_upload_issue(self) -> str:
        """
        Generate diagnostic info when file upload fails.

        Returns:
            Diagnostic report with page structure and tested selectors
        """
        try:
            from npdb.automation.playwright.locator import diagnose_upload_selector
            return await diagnose_upload_selector(self.page)
        except Exception as e:
            return f"Could not generate diagnosis: {e}"

    async def _find_file_input_selector_by_type(self, file_type: str) -> Optional[str]:
        """
        Find file input selector specific to file type (TSV or JSON).

        Args:
            file_type: "tsv" or "json"

        Returns:
            Working selector string, or None if not found
        """
        file_type = file_type.lower()

        if file_type == "tsv":
            fallback_selectors = [
                "input[accept*='tsv']",
                "input[data-cy='datatable-upload-input']",
                "input[type='file'][accept='.tsv']",
                "input[accept*='.tsv']",
            ]
        elif file_type == "json":
            fallback_selectors = [
                "input[accept*='json']",
                "input[data-cy='datadictionary-upload-input']",
                "input[type='file'][accept='.json']",
                "input[accept*='.json']",
            ]
        else:
            return None

        for selector in fallback_selectors:
            try:
                locator = self.page.locator(selector)
                if await locator.count() > 0:
                    print(
                        f"✓ Found {file_type.upper()} file input: {selector}")
                    return selector
            except:
                continue

        return None

    async def upload_file(self, file_path: Path, file_type: str = "tsv", selector: Optional[str] = None) -> None:
        """
        Upload a file (TSV data or JSON dictionary) with type-aware selector discovery.

        Includes:
        - Auto-detection of file type from extension (or explicit file_type param)
        - Type-specific selector discovery (handles multiple file inputs on page)
        - Retry logic with exponential backoff (3 attempts)
        - Specific selectors for TSV vs JSON uploads
        - Diagnostic output on failure

        Args:
            file_path: Path to file to upload. Must exist.
            file_type: Type of file - "tsv" (default) or "json" for phenotype dictionary.
                      Auto-detected from file extension if not provided.
            selector: CSS/XPath selector for file input element.
                     If None, will auto-discover using type-specific patterns.

        Raises:
            FileNotFoundError: If file doesn't exist.
            RuntimeError: If upload fails after exhausting all retries and fallbacks.
        """
        if not self.page:
            raise RuntimeError("Browser not launched. Call launch() first.")

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Auto-detect file type from extension if not explicitly provided
        if file_type.lower() not in ("tsv", "json"):
            suffix = file_path.suffix.lower()
            if suffix in (".tsv", ".csv"):
                file_type = "tsv"
            elif suffix == ".json":
                file_type = "json"
            else:
                raise ValueError(
                    f"Unknown file type: {suffix}. Expected .tsv or .json")

        # Auto-discover selector if not provided
        if not selector:
            discovered = await self._find_file_input_selector_by_type(file_type)
            if discovered:
                selector = discovered
            else:
                print(
                    f"⚠ Could not auto-discover {file_type.upper()} file input selector")
                print(await self._diagnose_upload_issue())
                raise RuntimeError(
                    f"No {file_type.upper()} file input element found on page. "
                    "Check diagnostic output above for page structure."
                )

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                # Use first() to avoid strict mode when multiple file inputs exist
                file_input = self.page.locator(selector).first

                # For file inputs: check element exists but don't wait for visibility
                # (file inputs are intentionally hidden, that's normal)
                # Wait for element to be in DOM (state="attached"), not visible
                await file_input.wait_for(timeout=10000, state="attached")

                # Perform upload directly on hidden input
                # Playwright handles interaction with hidden file inputs
                await file_input.set_input_files(str(file_path))

                # Wait for file to be processed (short wait, not networkidle)
                await asyncio.sleep(2.0)

                print(
                    f"✓ {file_type.upper()} upload successful on attempt {attempt}")
                return

            except Exception as e:
                if attempt < max_attempts:
                    wait_time = min(1 * (2 ** (attempt - 1)),
                                    5)  # 1, 2, 5 seconds
                    print(
                        f"⚠ {file_type.upper()} upload attempt {attempt}/{max_attempts} failed: {e}")
                    print(f"  Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    print(
                        f"✗ {file_type.upper()} upload failed after {max_attempts} attempts on selector: {selector}")
                    print("\n" + await self._diagnose_upload_issue())
                    raise RuntimeError(
                        f"Failed to upload {file_type.upper()} file to '{selector}' after {max_attempts} attempts: {e}"
                    ) from e

    async def click(self, selector: str, delay: int = 100) -> None:
        """
        Click an element with manual retry logic for transient failures.

        Args:
            selector: CSS/XPath selector.
            delay: Delay between mousedown and mouseup (ms).

        Raises:
            RuntimeError: If click fails after retries.
        """
        if not self.page:
            raise RuntimeError("Browser not launched.")

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                await self.page.click(selector, delay=delay)
                print(f"✓ Click successful on attempt {attempt}")
                return
            except Exception as e:
                if attempt < max_attempts:
                    wait_time = min(0.5 * (2 ** (attempt - 1)), 3)
                    print(
                        f"⚠ Click attempt {attempt}/{max_attempts} failed, retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise RuntimeError(
                        f"Failed to click '{selector}' after {max_attempts} attempts: {e}") from e

    async def fill(self, selector: str, text: str) -> None:
        """
        Fill a text input field.

        Args:
            selector: CSS/XPath selector.
            text: Text to fill.
        """
        if not self.page:
            raise RuntimeError("Browser not launched.")

        await self.page.fill(selector, text)

    async def select_option(self, selector: str, value: str) -> None:
        """
        Select an option from a select element.

        Args:
            selector: CSS/XPath selector.
            value: Option value to select.
        """
        if not self.page:
            raise RuntimeError("Browser not launched.")

        await self.page.select_option(selector, value)

    async def wait_for_selector(self, selector: str) -> None:
        """
        Wait for element to appear in DOM with manual retry for transient failures.

        Args:
            selector: CSS/XPath selector.

        Raises:
            RuntimeError: If selector not found within timeout.
        """
        if not self.page:
            raise RuntimeError("Browser not launched.")

        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                # 10s per attempt
                await self.page.wait_for_selector(selector, timeout=10000)
                return
            except Exception as e:
                if attempt < max_attempts:
                    wait_time = min(1 * (2 ** (attempt - 1)), 3)
                    print(
                        f"⚠ Selector not found, attempt {attempt}/{max_attempts}, retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise RuntimeError(
                        f"Selector not found within timeout: '{selector}'") from e

    async def wait_for_navigation(self) -> None:
        """
        Wait for page navigation to complete.

        Uses 'load' state instead of 'networkidle' to avoid hanging on slow networks.
        Useful after clicking Next/Submit buttons.
        """
        if not self.page:
            raise RuntimeError("Browser not launched.")

        try:
            # Use 'load' instead of 'networkidle' to avoid hanging
            # 30 second timeout
            await self.page.wait_for_load_state("load", timeout=30000)
        except Exception as e:
            # Don't fail hard - page might be functional even if load state didn't fire
            print(f"⚠ Warning: Page load state not confirmed: {e}")

    async def wait_for_download(self, expected_filename: Optional[str] = None, timeout: Optional[int] = None) -> Path:
        """
        Wait for a file to be downloaded and return its path.

        Uses Playwright's download event to detect and manage downloads.
        The download is saved to the artifacts_dir if available, else a temp directory.

        Args:
            expected_filename: Optional expected filename pattern (for validation).
            timeout: Timeout in milliseconds. If None, uses self.timeout.

        Returns:
            Path to the downloaded file.

        Raises:
            RuntimeError: If download doesn't complete or timeout occurs.
            TimeoutError: If timeout exceeded before download.
        """
        if not self.page:
            raise RuntimeError("Browser not launched.")

        timeout_ms = timeout if timeout is not None else self.timeout

        # Determine download directory
        download_dir = self.artifacts_dir if self.artifacts_dir else Path.home() / \
            ".cache" / "npdb-downloads"
        download_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Set context to save downloads to specific directory
            # Note: This requires context to have a download path configured
            # For now, we'll use event-based detection
            start_time = time.time()
            elapsed_ms = 0

            async with self.page.context.expect_download() as download_info_coro:
                # Wait for download event (this is set up by caller via click() etc.)
                # The context manager will capture the download
                while elapsed_ms < timeout_ms:
                    try:
                        download = await asyncio.wait_for(
                            download_info_coro.value,
                            timeout=(timeout_ms - elapsed_ms) / 1000.0
                        )
                        downloaded_path = Path(download.path)

                        # Move to output directory if needed
                        if download.suggested_filename:
                            output_path = download_dir / download.suggested_filename
                            downloaded_path.replace(output_path)
                            return output_path
                        return downloaded_path

                    except asyncio.TimeoutError:
                        elapsed_ms = int((time.time() - start_time) * 1000)
                        if elapsed_ms >= timeout_ms:
                            raise TimeoutError(
                                f"Download did not complete within {timeout_ms}ms"
                            )
                        await asyncio.sleep(0.1)

        except TimeoutError as e:
            raise TimeoutError(f"Download timeout: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Failed to wait for download: {e}") from e

    async def get_text(self, selector: str) -> str:
        """
        Get text content of an element.

        Args:
            selector: CSS/XPath selector.

        Returns:
            Text content.
        """
        if not self.page:
            raise RuntimeError("Browser not launched.")

        return await self.page.text_content(selector) or ""

    async def screenshot(self, path: Optional[Path] = None) -> bytes:
        """
        Take a screenshot of current page state.

        Args:
            path: Optional path to save screenshot.

        Returns:
            Screenshot bytes.
        """
        if not self.page:
            raise RuntimeError("Browser not launched.")

        screenshot_bytes = await self.page.screenshot()

        if path:
            path.write_bytes(screenshot_bytes)

        return screenshot_bytes

    async def capture_failure_artifacts(self, reason: str) -> None:
        """
        Capture artifacts (screenshot, trace) on failure.

        Args:
            reason: Description of failure.
        """
        if not self.artifacts_dir:
            return

        try:
            # Screenshot
            screenshot_path = self.artifacts_dir / f"failure_{reason}.png"
            await self.screenshot(screenshot_path)

            # Trace
            if self.context and self.trace_path:
                await self.context.tracing.stop(path=self.trace_path)
        except Exception as e:
            print(f"Warning: Failed to capture artifacts: {e}")

    async def cleanup(self) -> None:
        """
        Close browser and cleanup resources.

        Safe to call even if not fully initialized.
        """
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            print(f"Warning: Cleanup error: {e}")

    async def __aenter__(self):
        """Async context manager entry."""
        await self.launch()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit with cleanup."""
        if exc_type:
            await self.capture_failure_artifacts(f"exception_{exc_type.__name__}")
        await self.cleanup()
        return False

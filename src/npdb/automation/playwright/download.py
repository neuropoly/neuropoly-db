"""
Download handling for annotation tool exports.

Detects, waits for, and manages file downloads from browser.
Supports headless and headed modes with different download behaviors.
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


class DownloadHandler:
    """
    Manages file downloads from browser.

    Handles:
    - Download start detection
    - File completion waiting
    - Download directory management
    - File moving/copying to output location
    """

    def __init__(self, timeout: int = 60):
        """
        Initialize download handler.

        Args:
            timeout: Timeout for download completion (seconds).
        """
        self.timeout = timeout
        self.download_started_at: Optional[datetime] = None
        self.last_download_path: Optional[Path] = None

    async def wait_for_download(
        self,
        page,
        button_selector: str,
        expected_filename: Optional[str] = None
    ) -> Path:
        """
        Click download button and wait for file to complete.

        Args:
            page: Playwright page object.
            button_selector: Selector for download button.
            expected_filename: Expected filename (optional).

        Returns:
            Path to downloaded file.

        Raises:
            RuntimeError: If download times out or fails.
        """
        try:
            # Set up listener for download event
            async with page.context.expect_download() as download_info:
                # Click the download button
                await page.click(button_selector)

            download = await download_info.value
            download_path = Path(await download.path())

            # Wait for file to be fully written
            await self._wait_for_file_complete(download_path)

            self.last_download_path = download_path
            self.download_started_at = datetime.now()

            return download_path

        except asyncio.TimeoutError as e:
            raise RuntimeError(f"Download timeout: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Download failed: {e}") from e

    async def _wait_for_file_complete(self, file_path: Path) -> None:
        """
        Wait for file to finish writing (size stable).

        Args:
            file_path: Path to file to monitor.

        Raises:
            RuntimeError: If file doesn't stabilize within timeout.
        """
        start_time = datetime.now()
        last_size = -1
        stable_counts = 0

        while (datetime.now() - start_time).total_seconds() < self.timeout:
            if file_path.exists():
                current_size = file_path.stat().st_size
                if current_size == last_size:
                    stable_counts += 1
                    if stable_counts >= 2:  # File size stable for 2 checks
                        return
                else:
                    stable_counts = 0
                last_size = current_size

            await asyncio.sleep(0.5)

        raise RuntimeError(
            f"File not complete within {self.timeout}s: {file_path}"
        )

    async def move_download(
        self,
        source_path: Path,
        dest_dir: Path,
        rename_to: Optional[str] = None
    ) -> Path:
        """
        Move downloaded file to output directory.

        Args:
            source_path: Path to downloaded file.
            dest_dir: Destination directory.
            rename_to: Optional new filename.

        Returns:
            Path to moved file.

        Raises:
            FileNotFoundError: If source file doesn't exist.
            RuntimeError: If move fails.
        """
        if not source_path.exists():
            raise FileNotFoundError(
                f"Downloaded file not found: {source_path}")

        dest_dir.mkdir(parents=True, exist_ok=True)

        # Determine destination filename
        dest_filename = rename_to or source_path.name
        dest_path = dest_dir / dest_filename

        try:
            # Move file (or copy if cross-filesystem)
            import shutil
            shutil.move(str(source_path), str(dest_path))
            return dest_path
        except Exception as e:
            raise RuntimeError(f"Failed to move download: {e}") from e

    async def get_last_download(self) -> Optional[Path]:
        """
        Get path to last downloaded file.

        Returns:
            Path to last download, or None if no downloads yet.
        """
        return self.last_download_path

    async def clear_history(self) -> None:
        """Clear download history."""
        self.last_download_path = None
        self.download_started_at = None


class DownloadDetector:
    """
    Detects and monitors downloads from browser context.

    Listens for download events and tracks file information.
    """

    def __init__(self):
        """Initialize detector."""
        self.downloads_detected = []
        self.active_downloads = {}

    async def setup_listener(self, context) -> None:
        """
        Set up listener for download events.

        Args:
            context: Playwright browser context.
        """
        def on_download(download):
            """Handle download event."""
            self.downloads_detected.append({
                "filename": download.suggested_filename,
                "url": download.url,
                "timestamp": datetime.now().isoformat()
            })

        context.on("download", on_download)

    async def get_all_downloads(self) -> list:
        """
        Get list of all detected downloads.

        Returns:
            List of download info dicts.
        """
        return self.downloads_detected

    async def clear_downloads(self) -> None:
        """Clear download history."""
        self.downloads_detected.clear()
        self.active_downloads.clear()


class ExpectedFileValidator:
    """
    Validates that downloaded files match expected structure.

    Checks for required phenotype output files.
    """

    PHENOTYPES_JSON = "phenotypes_annotations.json"
    PHENOTYPES_SIDECAR = "phenotypes_provenance.json"

    @staticmethod
    async def validate_phenotypes_json(file_path: Path) -> bool:
        """
        Validate phenotypes_annotations.json structure.

        Args:
            file_path: Path to JSON file.

        Returns:
            True if valid structure, False otherwise.
        """
        if not file_path.exists():
            return False

        try:
            with open(file_path, "r") as f:
                data = json.load(f)

            # Check required keys
            required_keys = ["@context"]
            for key in required_keys:
                if key not in data:
                    return False

            return True
        except Exception:
            return False

    @staticmethod
    async def validate_phenotypes_sidecar(file_path: Path) -> bool:
        """
        Validate phenotypes_provenance.json structure.

        Args:
            file_path: Path to provenance JSON file.

        Returns:
            True if valid structure, False otherwise.
        """
        if not file_path.exists():
            return False

        try:
            with open(file_path, "r") as f:
                data = json.load(f)

            # Check required keys
            required_keys = ["run_id", "mode", "timestamp", "per_column"]
            for key in required_keys:
                if key not in data:
                    return False

            return True
        except Exception:
            return False

    @staticmethod
    async def validate_output_directory(output_dir: Path) -> Dict[str, bool]:
        """
        Validate output directory has expected files.

        Args:
            output_dir: Output directory path.

        Returns:
            Dict of filename -> exists (bool).
        """

        expected_files = {
            ExpectedFileValidator.PHENOTYPES_JSON: output_dir / ExpectedFileValidator.PHENOTYPES_JSON,
            ExpectedFileValidator.PHENOTYPES_SIDECAR: output_dir / ExpectedFileValidator.PHENOTYPES_SIDECAR,
        }

        results = {}
        for name, path in expected_files.items():
            results[name] = path.exists()

        return results

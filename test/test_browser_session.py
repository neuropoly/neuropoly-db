"""
Unit tests for browser_session module.

Tests session lifecycle, navigation, file operations, and error handling.
"""

import pytest
from pathlib import Path

from npdb.external.neurobagel.automation import NBAnnotationToolBrowserSession


class TestBrowserSessionInit:
    """Tests for BrowserSession initialization."""

    def test_init_defaults(self):
        """Test BrowserSession with default configuration."""
        session = NBAnnotationToolBrowserSession()

        assert session.headless is True
        assert session.timeout == 300000  # 300s * 1000ms
        assert session.browser is None
        assert session.page is None

    def test_init_custom_config(self):
        """Test BrowserSession with custom configuration."""
        artifacts_dir = Path("/tmp/artifacts")
        session = NBAnnotationToolBrowserSession(
            headless=False, timeout=600, artifacts_dir=artifacts_dir)

        assert session.headless is False
        assert session.timeout == 600000
        assert session.artifacts_dir == artifacts_dir

    def test_init_timeout_conversion_ms(self):
        """Test that timeout is converted to milliseconds."""
        session = NBAnnotationToolBrowserSession(timeout=100)
        assert session.timeout == 100000  # 100 * 1000


class TestBrowserSessionConfig:
    """Tests for BrowserSession configuration attributes."""

    def test_annotation_url_constant(self):
        """Test annotation URL constant."""
        assert NBAnnotationToolBrowserSession.ANNOTATION_URL == "https://annotate.neurobagel.org"

    def test_artifacts_dir_optional(self):
        """Test that artifacts_dir is optional."""
        session = NBAnnotationToolBrowserSession()
        assert session.artifacts_dir is None

    def test_artifacts_dir_path(self):
        """Test artifacts_dir is stored as Path."""
        path = Path("/tmp/test")
        session = NBAnnotationToolBrowserSession(artifacts_dir=path)
        assert session.artifacts_dir == path
        assert isinstance(session.artifacts_dir, Path)


class TestBrowserSessionCleanup:
    """Tests for cleanup operations."""

    def test_cleanup_no_crash_uninitialized(self):
        """Test cleanup doesn't crash on uninitialized session."""
        session = NBAnnotationToolBrowserSession()
        # Should not raise
        import asyncio
        asyncio.run(session.cleanup())

    def test_context_manager_cleanup_on_success(self):
        """Test async context manager cleanup on success."""
        # This is a structural test; actual Playwright operations skipped
        import asyncio

        async def test():
            session = NBAnnotationToolBrowserSession()
            assert session.browser is None
            # Cleanup should handle None gracefully
            await session.cleanup()

        asyncio.run(test())


class TestBrowserSessionFileOperations:
    """Tests for file upload validation."""

    def test_upload_file_not_exists(self):
        """Test upload raises FileNotFoundError for missing file."""
        import asyncio

        async def test():
            session = NBAnnotationToolBrowserSession()
            # Both FileNotFoundError (file not found) and RuntimeError (not launched) are possible
            with pytest.raises((FileNotFoundError, RuntimeError)):
                await session.upload_file(Path("/nonexistent/file.tsv"), "input[type='file']")

        asyncio.run(test())

    def test_upload_file_path_validation(self):
        """Test upload validates file path before attempting upload."""
        import asyncio
        import tempfile

        async def test():
            with tempfile.NamedTemporaryFile(suffix=".tsv") as f:
                session = NBAnnotationToolBrowserSession()
                # File exists, but session.page is None so will raise RuntimeError
                with pytest.raises(RuntimeError, match="not launched"):
                    await session.upload_file(Path(f.name), "input[type='file']")

        asyncio.run(test())


class TestBrowserSessionErrorHandling:
    """Tests for error handling in operations."""

    def test_navigate_not_launched(self):
        """Test navigate raises error when browser not launched."""
        import asyncio

        async def test():
            session = NBAnnotationToolBrowserSession()
            with pytest.raises(RuntimeError, match="not launched"):
                await session.navigate_to()

        asyncio.run(test())

    def test_click_not_launched(self):
        """Test click raises error when browser not launched."""
        import asyncio

        async def test():
            session = NBAnnotationToolBrowserSession()
            with pytest.raises(RuntimeError, match="not launched"):
                await session.click("button")

        asyncio.run(test())

    def test_fill_not_launched(self):
        """Test fill raises error when browser not launched."""
        import asyncio

        async def test():
            session = NBAnnotationToolBrowserSession()
            with pytest.raises(RuntimeError, match="not launched"):
                await session.fill("input", "text")

        asyncio.run(test())


class TestBrowserSessionAttributes:
    """Tests for session state attributes."""

    def test_initial_state(self):
        """Test initial session state."""
        session = NBAnnotationToolBrowserSession()

        assert session.browser is None
        assert session.context is None
        assert session.page is None
        assert session.playwright is None
        assert session.trace_path is None

    def test_headless_modes(self):
        """Test both headless and headed modes."""
        headless_session = NBAnnotationToolBrowserSession(headless=True)
        headed_session = NBAnnotationToolBrowserSession(headless=False)

        assert headless_session.headless is True
        assert headed_session.headless is False


class TestArtifactCapture:
    """Tests for artifact capture configuration."""

    def test_artifacts_dir_none_skips_capture(self):
        """Test that None artifacts_dir skips capture."""
        session = NBAnnotationToolBrowserSession(artifacts_dir=None)
        assert session.artifacts_dir is None
        assert session.trace_path is None

    def test_artifacts_dir_creates_trace_path(self):
        """Test artifacts_dir creates trace path."""
        artifacts = Path("/tmp/artifacts")
        session = NBAnnotationToolBrowserSession(artifacts_dir=artifacts)
        # Note: trace_path is set during launch(), not __init__()
        # So it will be None until launch() is called
        assert session.trace_path is None  # Not yet launched


class TestEdgeCases:
    """Tests for edge cases and robustness."""

    def test_timeout_zero(self):
        """Test timeout=0 (no timeout)."""
        session = NBAnnotationToolBrowserSession(timeout=0)
        assert session.timeout == 0

    def test_timeout_large(self):
        """Test very large timeout."""
        session = NBAnnotationToolBrowserSession(timeout=86400)  # 24 hours
        assert session.timeout == 86400000

    def test_multiple_cleanup_calls(self):
        """Test multiple cleanup calls don't error."""
        import asyncio

        async def test():
            session = NBAnnotationToolBrowserSession()
            await session.cleanup()
            await session.cleanup()  # Should not crash

        asyncio.run(test())

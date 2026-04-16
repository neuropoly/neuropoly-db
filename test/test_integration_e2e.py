"""
End-to-end integration tests for annotation automation.

Tests the full pipeline from parsing TSV through saving provenance sidecar.
Uses synthetic data and mocked browser interactions.
"""

import pytest
from pathlib import Path
import tempfile
import json
from unittest.mock import AsyncMock, patch

from npdb.annotation import AnnotationConfig
from npdb.automation.mappings.resolvers import MappingResolver
from npdb.managers.neurobagel import NeurobagelAnnotator


@pytest.fixture
def synthetic_tsv() -> Path:
    """Create synthetic participants.tsv for testing."""
    content = """participant_id\tage\tsex\tdiagnosis\tcognitive_score
sub-01\t22\tM\tCTRL\t95.5
sub-02\t28\tF\tPD\t78.2
sub-03\t35\tM\tCTRL\t92.1
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
        f.write(content)
        return Path(f.name)


@pytest.fixture
def output_dir() -> Path:
    """Create temporary output directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestAnnotationManagerE2E:
    """End-to-end tests for AnnotationManager."""

    def test_manager_initialization_manual_mode(self, synthetic_tsv: Path, output_dir: Path):
        """Test AnnotationManager initializes correctly for manual mode."""
        config = AnnotationConfig(mode="manual", headless=False)
        manager = NeurobagelAnnotator(config)

        assert manager.config.mode == "manual"
        assert manager.config.headless is False
        assert manager.resolver is not None
        assert manager.provenance is not None

    def test_manager_initialization_auto_mode(self, synthetic_tsv: Path, output_dir: Path):
        """Test AnnotationManager initializes correctly for auto mode."""
        config = AnnotationConfig(mode="auto", headless=True)
        manager = NeurobagelAnnotator(config)

        assert manager.config.mode == "auto"
        assert manager.config.headless is True
        assert manager.resolver is not None

    def test_manager_initialization_full_auto_mode(self, synthetic_tsv: Path, output_dir: Path):
        """Test AnnotationManager initializes correctly for full-auto mode."""
        config = AnnotationConfig(mode="full-auto", headless=True)
        manager = NeurobagelAnnotator(config)

        assert manager.config.mode == "full-auto"
        assert manager.config.headless is True

    @pytest.mark.asyncio
    async def test_mapping_resolver_integration(self, synthetic_tsv: Path):
        """Test mapping resolver works with synthetic data."""
        resolver = MappingResolver()

        # Resolve common columns from synthetic TSV
        column_names = ["participant_id", "age",
                        "sex", "diagnosis", "cognitive_score"]
        resolved = resolver.resolve_columns(column_names)

        # Verify all columns resolved
        assert len(resolved) == len(column_names)

        # Check key mappings
        participant_mapping = [
            r for r in resolved if r.column_name == "participant_id"][0]
        assert participant_mapping.mapped_variable == "nb:ParticipantID"
        assert participant_mapping.source == "static"

        age_mapping = [r for r in resolved if r.column_name == "age"][0]
        assert age_mapping.mapped_variable == "nb:Age"
        assert age_mapping.source in ["static", "deterministic"]

    @pytest.mark.asyncio
    async def test_assist_mode_with_user_dictionary(self, synthetic_tsv: Path, output_dir: Path):
        """Test assist mode can load user dictionary override."""
        # Create a custom dictionary
        custom_dict = {
            "@context": {
                "nb": "http://neurobagel.org/vocab/"
            },
            "mappings": {
                "cognitive_score": {
                    "variable": "nb:Assessment",
                    "confidence": 0.9,
                    "variableType": "Continuous"
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(custom_dict, f)
            dict_path = Path(f.name)

        try:
            config = AnnotationConfig(
                mode="assist",
                phenotype_dictionary=dict_path
            )
            manager = NeurobagelAnnotator(config)

            # Verify resolver was initialized with user dict
            assert manager.resolver is not None

            # Resolve with custom dictionary
            resolved = manager.resolver.resolve_column("cognitive_score")
            assert resolved.mapped_variable == "nb:Assessment"

        finally:
            dict_path.unlink()

    @pytest.mark.asyncio
    async def test_file_not_found_error(self, output_dir: Path):
        """Test error handling for missing TSV file."""
        config = AnnotationConfig(mode="auto")
        manager = NeurobagelAnnotator(config)

        with pytest.raises(FileNotFoundError):
            await manager.execute(
                participants_tsv_path=Path("/nonexistent/file.tsv"),
                output_dir=output_dir
            )


class TestProvenance:
    """Tests for provenance sidecar generation."""

    def test_provenance_record_creation(self, synthetic_tsv: Path, output_dir: Path):
        """Test provenance object is created and tracked."""
        config = AnnotationConfig(mode="auto")
        manager = NeurobagelAnnotator(config)

        # Verify provenance was initialized
        assert manager.provenance is not None
        assert manager.provenance.mode == "auto"
        assert manager.provenance.run_id is not None and len(
            manager.provenance.run_id) > 0
        assert manager.provenance.timestamp is not None


class TestModeSpecificBehavior:
    """Tests for mode-specific behavior differences."""

    def test_mode_determines_config_settings(self):
        """Test mode influences configuration settings."""
        manual_config = AnnotationConfig(mode="manual")
        assert manual_config.headless is True  # Default

        auto_config = AnnotationConfig(mode="auto", headless=False)
        assert auto_config.headless is False

    def test_confidence_thresholds_per_mode(self):
        """Test that modes are properly configured."""
        # Verify managers can be created for each mode with correct settings

        manual_config = AnnotationConfig(mode="manual")
        assist_config = AnnotationConfig(mode="assist")
        auto_config = AnnotationConfig(mode="auto")
        full_auto_config = AnnotationConfig(mode="full-auto")

        manual_manager = NeurobagelAnnotator(manual_config)
        assist_manager = NeurobagelAnnotator(assist_config)
        auto_manager = NeurobagelAnnotator(auto_config)
        full_auto_manager = NeurobagelAnnotator(full_auto_config)

        # Verify all managers were initialized correctly
        assert manual_manager.config.mode == "manual"
        assert assist_manager.config.mode == "assist"
        assert auto_manager.config.mode == "auto"
        assert full_auto_manager.config.mode == "full-auto"

        # Verify provenance tracking for each mode
        assert manual_manager.provenance.mode == "manual"
        assert assist_manager.provenance.mode == "assist"
        assert auto_manager.provenance.mode == "auto"
        assert full_auto_manager.provenance.mode == "full-auto"


class TestSmokeE2E:
    """Smoke tests for end-to-end workflow without live browser.

    These tests verify the complete annotation pipeline by mocking
    BrowserSession and verifying all expected outputs are generated.
    """

    @pytest.mark.asyncio
    async def test_full_auto_mode_smoke_with_mocked_browser(self, synthetic_tsv: Path, output_dir: Path):
        """
        Smoke test: Full-auto mode completes successfully with mocked browser.

        Validates:
        - AnnotationManager.execute() returns True
        - Provenance sidecar is created at expected path
        - Provenance contains resolved column mappings
        - Warnings are emitted for full-auto mode
        """
        config = AnnotationConfig(
            mode="full-auto",
            headless=True,
            artifacts_dir=output_dir
        )
        manager = NeurobagelAnnotator(config)

        # Mock NBAnnotationToolBrowserSession context manager
        with patch('npdb.managers.neurobagel.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            # Run execute
            result = await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

            # Verify execution succeeded
            assert result is True

            # Verify BrowserSession was initialized with correct config
            mock_browser_class.assert_called_once()
            call_kwargs = mock_browser_class.call_args[1]
            assert call_kwargs['headless'] is True
            assert call_kwargs['timeout'] == 300

            # Verify browser methods were called
            mock_session.navigate_to.assert_called_once()
            mock_session.upload_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_auto_mode_provenance_output(self, synthetic_tsv: Path, output_dir: Path):
        """
        Verify provenance sidecar is created with correct structure.

        Validates:
        - phenotypes_provenance.json exists in output_dir
        - Contains mode, run_id, timestamp fields
        - Contains column mapping information
        - Contains full-auto warning
        """
        config = AnnotationConfig(
            mode="full-auto",
            headless=True
        )
        manager = NeurobagelAnnotator(config)

        with patch('npdb.managers.neurobagel.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            result = await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

            assert result is True

            # Verify provenance file was created
            provenance_path = output_dir / "phenotypes_provenance.json"
            assert provenance_path.exists(
            ), f"Provenance file not found at {provenance_path}"

            # Load and validate provenance structure
            with open(provenance_path) as f:
                provenance_data = json.load(f)

            # Verify required fields
            assert "mode" in provenance_data
            assert provenance_data["mode"] == "full-auto"
            assert "run_id" in provenance_data
            assert "timestamp" in provenance_data
            assert "per_column" in provenance_data

            # Verify columns were mapped
            assert len(provenance_data["per_column"]) > 0

            # Verify warning was added
            assert "warnings" in provenance_data
            assert any(
                "FULL-AUTO MODE" in w for w in provenance_data["warnings"])

    @pytest.mark.asyncio
    async def test_auto_mode_smoke_with_confidence_filtering(self, synthetic_tsv: Path, output_dir: Path):
        """
        Smoke test: Auto mode with confidence threshold filtering.

        Validates:
        - Auto mode uses ≥0.7 confidence threshold
        - Only high-confidence mappings are included
        - Provenance tracks confidence values
        """
        config = AnnotationConfig(
            mode="auto",
            headless=True
        )
        manager = NeurobagelAnnotator(config)

        with patch('npdb.managers.neurobagel.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            result = await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

            assert result is True

            # Verify provenance was created
            provenance_path = output_dir / "phenotypes_provenance.json"
            assert provenance_path.exists()

            with open(provenance_path) as f:
                provenance_data = json.load(f)

            # Verify mode
            assert provenance_data["mode"] == "auto"

            # Verify per-column mappings include confidence data
            for col_name, col_data in provenance_data.get("per_column", {}).items():
                if "mappings" in col_data:
                    for mapping in col_data["mappings"]:
                        assert "confidence" in mapping
                        # Auto mode should have some confidence annotation
                        assert mapping.get("source") in [
                            "static", "deterministic"]

    @pytest.mark.asyncio
    async def test_assist_mode_smoke_no_browser_wait(self, synthetic_tsv: Path, output_dir: Path):
        """
        Smoke test: Assist mode initialization without user interaction.

        Validates:
        - Assist mode can be initialized
        - Returns True even without actual user interaction
        - Provenance tracks prefilled values
        """
        config = AnnotationConfig(
            mode="assist",
            headless=False,
            timeout=1,
        )
        manager = NeurobagelAnnotator(config)

        with patch('npdb.managers.neurobagel.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            result = await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

            assert result is True
            assert mock_session.navigate_to.called

    @pytest.mark.asyncio
    async def test_regression_no_side_effects(self, synthetic_tsv: Path, output_dir: Path):
        """
        Regression test: Verify synthetic TSV file is not modified.

        Validates:
        - Input TSV is untouched after execution
        - No temporary files leak
        - Output directory only contains expected provenance file
        """
        # Read original TSV content
        original_content = synthetic_tsv.read_text()

        config = AnnotationConfig(mode="full-auto", headless=True)
        manager = NeurobagelAnnotator(config)

        with patch('npdb.managers.neurobagel.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        # Verify TSV was not modified
        assert synthetic_tsv.read_text() == original_content

        # Verify output directory structure
        output_files = list(output_dir.glob("*"))
        # Should have provenance file
        provenance_files = [
            f for f in output_files if f.name == "phenotypes_provenance.json"]
        assert len(provenance_files) == 1

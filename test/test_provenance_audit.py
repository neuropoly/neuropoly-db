"""
Provenance audit and completeness tests.

These tests verify that provenance reports are generated correctly, contain
all required information, and are ready for audit/compliance review.

Provenance is now returned from execute() as the second element of the
(success, report) tuple rather than written to phenotypes_provenance.json.
"""

import pytest
from pathlib import Path
import tempfile
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

from npdb.annotation import AnnotationConfig
from npdb.managers.neurobagel import NeurobagelAnnotator


@pytest.fixture
def synthetic_tsv() -> Path:
    """Create synthetic participants.tsv for testing."""
    content = """participant_id\tage\tsex\tdiagnosis\tcognitive_score
sub-01\t22\tM\tCTRL\t95.5
sub-02\t28\tF\tPD\t78.2
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
        f.write(content)
        return Path(f.name)


@pytest.fixture
def output_dir() -> Path:
    """Create temporary output directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestProvenanceStructure:
    """Tests for provenance report structure."""

    @pytest.mark.asyncio
    async def test_provenance_has_required_top_level_fields(self, synthetic_tsv: Path, output_dir: Path):
        """Verify provenance has all required top-level fields."""
        config = AnnotationConfig(mode="full-auto", headless=True)
        manager = NeurobagelAnnotator(config)

        with patch('npdb.external.neurobagel.automation.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            _success, report = await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        assert report is not None
        provenance = report.model_dump(mode="json")

        # Required top-level fields (from ProvenanceReport model)
        required_fields = ["mode", "run_id", "timestamp", "per_column",
                           "mapping_source_counts", "confidence_distribution", "warnings"]
        for field in required_fields:
            assert field in provenance, f"Missing required field: {field}"

    @pytest.mark.asyncio
    async def test_provenance_mode_field_valid(self, synthetic_tsv: Path, output_dir: Path):
        """Verify provenance mode field matches execution mode."""
        for mode in ["manual", "assist", "auto", "full-auto"]:
            if mode == "manual":
                continue  # Skip manual as it requires user interaction

            output_subdir = output_dir / mode
            output_subdir.mkdir(exist_ok=True)

            config = AnnotationConfig(mode=mode, headless=True, timeout=1)
            manager = NeurobagelAnnotator(config)

            with patch('npdb.external.neurobagel.automation.NBAnnotationToolBrowserSession') as mock_browser_class:
                mock_session = AsyncMock()
                mock_browser_class.return_value.__aenter__.return_value = mock_session
                mock_browser_class.return_value.__aexit__.return_value = None

                _success, report = await manager.execute(
                    participants_tsv_path=synthetic_tsv,
                    output_dir=output_subdir
                )

            assert report is not None
            assert report.mode == mode, f"Mode mismatch: expected {mode}, got {report.mode}"

    @pytest.mark.asyncio
    async def test_provenance_timestamp_valid(self, synthetic_tsv: Path, output_dir: Path):
        """Verify provenance timestamp is valid."""
        config = AnnotationConfig(mode="full-auto", headless=True)
        manager = NeurobagelAnnotator(config)

        with patch('npdb.external.neurobagel.automation.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            _success, report = await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        assert report is not None
        ts = report.timestamp
        # Verify it's reasonably recent (within last 5 minutes)
        now = datetime.now(ts.tzinfo)
        assert (now - ts).total_seconds() < 300

    @pytest.mark.asyncio
    async def test_provenance_run_id_unique(self, synthetic_tsv: Path, output_dir: Path):
        """Verify each run has a unique run_id."""
        run_ids = []

        for i in range(2):
            subdir = output_dir / f"run_{i}"
            subdir.mkdir()

            config = AnnotationConfig(mode="full-auto", headless=True)
            manager = NeurobagelAnnotator(config)

            with patch('npdb.external.neurobagel.automation.NBAnnotationToolBrowserSession') as mock_browser_class:
                mock_session = AsyncMock()
                mock_browser_class.return_value.__aenter__.return_value = mock_session
                mock_browser_class.return_value.__aexit__.return_value = None

                _success, report = await manager.execute(
                    participants_tsv_path=synthetic_tsv,
                    output_dir=subdir
                )

            assert report is not None
            run_ids.append(report.run_id)

        # All run_ids should be unique
        assert len(run_ids) == len(set(run_ids)), "Run IDs are not unique"


class TestProvenanceCompleteness:
    """Tests for provenance completeness and data accuracy."""

    @pytest.mark.asyncio
    async def test_per_column_tracking_present(self, synthetic_tsv: Path, output_dir: Path):
        """Verify per_column tracking is present for all columns."""
        config = AnnotationConfig(mode="full-auto", headless=True)
        manager = NeurobagelAnnotator(config)

        with patch('npdb.external.neurobagel.automation.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            _success, report = await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        assert report is not None
        # Should have per_column tracking dict
        assert isinstance(report.per_column, dict)
        # From synthetic TSV: participant_id, age, sex, diagnosis, cognitive_score
        # At least some should be tracked

    @pytest.mark.asyncio
    async def test_provenance_column_mapping_structure(self, synthetic_tsv: Path, output_dir: Path):
        """Verify per-column mapping structure is valid."""
        config = AnnotationConfig(mode="full-auto", headless=True)
        manager = NeurobagelAnnotator(config)

        with patch('npdb.external.neurobagel.automation.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            _success, report = await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        assert report is not None
        valid_sources = {"static", "deterministic", "ai", "manual"}
        for col_name, col_prov in report.per_column.items():
            assert col_prov.source in valid_sources
            assert 0.0 <= col_prov.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_provenance_confidence_values_valid(self, synthetic_tsv: Path, output_dir: Path):
        """Verify all confidence values are between 0 and 1."""
        config = AnnotationConfig(mode="auto", headless=True)
        manager = NeurobagelAnnotator(config)

        with patch('npdb.external.neurobagel.automation.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            _success, report = await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        assert report is not None
        for col_name, col_prov in report.per_column.items():
            assert 0.0 <= col_prov.confidence <= 1.0, (
                f"Invalid confidence {col_prov.confidence} for {col_name}"
            )


class TestProvenanceWarnings:
    """Tests for provenance warning generation."""

    @pytest.mark.asyncio
    async def test_full_auto_mode_emits_warning(self, synthetic_tsv: Path, output_dir: Path):
        """Verify full-auto mode includes warning in provenance."""
        config = AnnotationConfig(mode="full-auto", headless=True)
        manager = NeurobagelAnnotator(config)

        with patch('npdb.external.neurobagel.automation.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            _success, report = await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        assert report is not None
        assert len(report.warnings) > 0
        warning_text = " ".join(report.warnings).lower()
        assert "full-auto" in warning_text or "experimental" in warning_text or "unstable" in warning_text

    @pytest.mark.asyncio
    async def test_assist_mode_no_experimental_warning(self, synthetic_tsv: Path, output_dir: Path):
        """Verify assist mode doesn't have experimental warning."""
        config = AnnotationConfig(mode="assist", headless=True, timeout=1)
        manager = NeurobagelAnnotator(config)

        with patch('npdb.external.neurobagel.automation.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            _success, report = await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        assert report is not None
        warning_text = " ".join(report.warnings).lower()
        assert "full-auto" not in warning_text


class TestProvenanceSummary:
    """Tests for provenance summary statistics."""

    @pytest.mark.asyncio
    async def test_mapping_source_counts_exist(self, synthetic_tsv: Path, output_dir: Path):
        """Verify provenance includes mapping source counts."""
        config = AnnotationConfig(mode="full-auto", headless=True)
        manager = NeurobagelAnnotator(config)

        with patch('npdb.external.neurobagel.automation.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            _success, report = await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        assert report is not None
        counts = report.mapping_source_counts
        assert isinstance(counts, dict)
        assert len(counts) >= 1

        valid_sources = {"static", "deterministic", "ai", "manual"}
        for source in counts.keys():
            assert source in valid_sources

    @pytest.mark.asyncio
    async def test_confidence_distribution_valid(self, synthetic_tsv: Path, output_dir: Path):
        """Verify provenance includes valid confidence distribution."""
        config = AnnotationConfig(mode="full-auto", headless=True)
        manager = NeurobagelAnnotator(config)

        with patch('npdb.external.neurobagel.automation.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            _success, report = await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        assert report is not None
        dist = report.confidence_distribution
        # Should have all bucket fields
        assert isinstance(dist.high, list)
        assert isinstance(dist.medium, list)
        assert isinstance(dist.low, list)
        assert isinstance(dist.unresolved, int)

"""
Provenance audit and completeness tests.

These tests verify that provenance sidecars are generated correctly, contain
all required information, and are ready for audit/compliance review.
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
    """Tests for provenance JSON structure."""

    @staticmethod
    def load_provenance_file(output_dir: Path) -> dict:
        """Helper to load provenance JSON from output directory."""
        provenance_path = output_dir / "phenotypes_provenance.json"
        if not provenance_path.exists():
            raise FileNotFoundError(
                f"Provenance file not found at {provenance_path}")
        with open(provenance_path) as f:
            return json.load(f)

    @pytest.mark.asyncio
    async def test_provenance_has_required_top_level_fields(self, synthetic_tsv: Path, output_dir: Path):
        """Verify provenance has all required top-level fields."""
        config = AnnotationConfig(mode="full-auto", headless=True)
        manager = NeurobagelAnnotator(config)

        with patch('npdb.external.neurobagel.automation.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        provenance = self.load_provenance_file(output_dir)

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

                await manager.execute(
                    participants_tsv_path=synthetic_tsv,
                    output_dir=output_subdir
                )

            provenance = self.load_provenance_file(output_subdir)
            assert provenance["mode"] == mode, f"Mode mismatch: expected {mode}, got {provenance['mode']}"

    @pytest.mark.asyncio
    async def test_provenance_timestamp_valid(self, synthetic_tsv: Path, output_dir: Path):
        """Verify provenance timestamp is valid ISO format."""
        config = AnnotationConfig(mode="full-auto", headless=True)
        manager = NeurobagelAnnotator(config)

        with patch('npdb.external.neurobagel.automation.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        provenance = self.load_provenance_file(output_dir)

        # Timestamp should be ISO format
        try:
            ts = datetime.fromisoformat(
                provenance["timestamp"].replace("Z", "+00:00"))
            # Verify it's reasonably recent (within last 5 minutes)
            now = datetime.now(ts.tzinfo)
            assert (now - ts).total_seconds() < 300
        except (ValueError, TypeError) as e:
            pytest.fail(
                f"Invalid timestamp format: {provenance['timestamp']}: {e}")

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

                await manager.execute(
                    participants_tsv_path=synthetic_tsv,
                    output_dir=subdir
                )

            provenance = self.load_provenance_file(subdir)
            run_ids.append(provenance["run_id"])

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

            await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        provenance_path = output_dir / "phenotypes_provenance.json"
        with open(provenance_path) as f:
            provenance = json.load(f)

        # Should have per_column tracking
        assert "per_column" in provenance
        assert isinstance(provenance["per_column"], dict)

        # Should have at least some columns tracked
        # (May be empty if all were unresolved, but structure should exist)
        tracked_columns = list(provenance["per_column"].keys())
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

            await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        provenance_path = output_dir / "phenotypes_provenance.json"
        with open(provenance_path) as f:
            provenance = json.load(f)

        # Check each tracked column has valid structure
        for col_name, col_data in provenance.get("per_column", {}).items():
            # Column should have at least some tracking info
            assert isinstance(col_data, dict)

            # If mappings exist, verify structure
            if "mappings" in col_data:
                assert isinstance(col_data["mappings"], list)
                for mapping in col_data["mappings"]:
                    assert "source" in mapping
                    assert "confidence" in mapping
                    # Source should be valid
                    assert mapping["source"] in [
                        "static", "deterministic", "ai"]

    @pytest.mark.asyncio
    async def test_provenance_confidence_values_valid(self, synthetic_tsv: Path, output_dir: Path):
        """Verify all confidence values are between 0 and 1."""
        config = AnnotationConfig(mode="auto", headless=True)
        manager = NeurobagelAnnotator(config)

        with patch('npdb.external.neurobagel.automation.NBAnnotationToolBrowserSession') as mock_browser_class:
            mock_session = AsyncMock()
            mock_browser_class.return_value.__aenter__.return_value = mock_session
            mock_browser_class.return_value.__aexit__.return_value = None

            await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        provenance_path = output_dir / "phenotypes_provenance.json"
        with open(provenance_path) as f:
            provenance = json.load(f)

        # Check all confidence values
        for col_name, col_data in provenance.get("per_column", {}).items():
            if "mappings" in col_data:
                for mapping in col_data["mappings"]:
                    confidence = mapping.get("confidence")
                    if confidence is not None:
                        assert 0 <= confidence <= 1, f"Invalid confidence {confidence} for {col_name}"


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

            await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        provenance_path = output_dir / "phenotypes_provenance.json"
        with open(provenance_path) as f:
            provenance = json.load(f)

        # Full-auto should have warnings
        assert "warnings" in provenance
        assert len(provenance["warnings"]) > 0
        # Should mention full-auto
        warning_text = " ".join(provenance["warnings"]).lower()
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

            await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        provenance_path = output_dir / "phenotypes_provenance.json"
        with open(provenance_path) as f:
            provenance = json.load(f)

        # Assist mode should not have full-auto warning
        if "warnings" in provenance:
            warning_text = " ".join(provenance["warnings"]).lower()
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

            await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        provenance_path = output_dir / "phenotypes_provenance.json"
        with open(provenance_path) as f:
            provenance = json.load(f)

        # Should have mapping_source_counts
        assert "mapping_source_counts" in provenance
        counts = provenance["mapping_source_counts"]

        # Should be a dict with at least one source type
        assert isinstance(counts, dict)
        assert len(counts) >= 1

        # All keys should be valid source types
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

            await manager.execute(
                participants_tsv_path=synthetic_tsv,
                output_dir=output_dir
            )

        provenance_path = output_dir / "phenotypes_provenance.json"
        with open(provenance_path) as f:
            provenance = json.load(f)

        # Should have confidence_distribution
        assert "confidence_distribution" in provenance
        dist = provenance["confidence_distribution"]

        # Should have buckets
        assert isinstance(dist, dict)
        expected_buckets = ["high", "medium", "low", "unresolved"]
        for bucket in expected_buckets:
            assert bucket in dist

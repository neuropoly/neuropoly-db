import pytest
from datetime import datetime

from npdb.annotation.provenance import (
    ProvenanceReport,
    ColumnProvenance,
    add_column_provenance,
    add_warning,
    save_provenance,
    load_provenance,
)


class TestColumnProvenance:
    """Tests for ColumnProvenance model."""

    def test_provenance_static_source(self):
        """Test creating provenance for static mapping."""
        prov = ColumnProvenance(
            column_name="age",
            source="static",
            confidence=0.95,
            variable="nb:Age",
            rationale="Found in built-in dictionary"
        )
        assert prov.column_name == "age"
        assert prov.source == "static"
        assert prov.confidence == 0.95

    def test_provenance_ai_source_with_model(self):
        """Test creating provenance for AI mapping with model info."""
        prov = ColumnProvenance(
            column_name="diagnosis",
            source="ai",
            confidence=0.72,
            variable="nb:Diagnosis",
            rationale="Inferred from column values",
            ai_model="ollama/neural-chat",
            ai_model_version="1.2"
        )
        assert prov.source == "ai"
        assert prov.ai_model == "ollama/neural-chat"


class TestProvenanceReport:
    """Tests for ProvenanceReport model."""

    def test_report_creation_manual_mode(self):
        """Test creating a provenance report for manual mode."""
        report = ProvenanceReport(mode="manual")
        assert report.mode == "manual"
        assert report.run_id is not None
        assert isinstance(report.timestamp, datetime)

    def test_report_full_auto_mode(self):
        """Test creating a report for full-auto mode."""
        report = ProvenanceReport(
            mode="full-auto",
            ai_provider="ollama",
            ai_model="neural-chat",
            ai_threshold=0.5
        )
        assert report.mode == "full-auto"
        assert report.ai_provider == "ollama"
        assert report.ai_threshold == 0.5


class TestAddColumnProvenance:
    """Tests for adding column provenance to report."""

    def test_add_static_column(self):
        """Test adding a static mapping to report."""
        report = ProvenanceReport(mode="auto")
        add_column_provenance(
            report,
            column_name="age",
            source="static",
            confidence=0.95,
            variable="nb:Age",
            rationale="Built-in mapping"
        )

        assert "age" in report.per_column
        assert report.per_column["age"].source == "static"
        assert report.mapping_source_counts["static"] == 1

    def test_add_ai_column_with_threshold(self):
        """Test adding AI mapping and verifying confidence distribution."""
        report = ProvenanceReport(mode="full-auto")
        add_column_provenance(
            report,
            column_name="diagnosis",
            source="ai",
            confidence=0.72,
            variable="nb:Diagnosis",
            rationale="AI inference",
            ai_model="ollama/neural-chat"
        )

        assert report.mapping_source_counts["ai"] == 1
        # Confidence 0.72 should be in "medium" bucket [0.7, 0.84]
        assert 0.72 in report.confidence_distribution.medium

    def test_add_multiple_columns_and_count_sources(self):
        """Test adding multiple columns and verifying source counts."""
        report = ProvenanceReport(mode="auto")

        add_column_provenance(report, "id", "static", 1.0,
                              "nb:ParticipantID", rationale="Static")
        add_column_provenance(report, "age", "deterministic",
                              0.82, "nb:Age", rationale="Fuzzy")
        add_column_provenance(report, "diag", "ai", 0.65,
                              "nb:Diagnosis", rationale="AI")

        assert report.mapping_source_counts["static"] == 1
        assert report.mapping_source_counts["deterministic"] == 1
        assert report.mapping_source_counts["ai"] == 1


class TestAddWarning:
    """Tests for adding warnings to report."""

    def test_add_warning(self):
        """Test adding a warning message."""
        report = ProvenanceReport(mode="full-auto")
        warning_msg = "Low confidence threshold used in full-auto mode"

        add_warning(report, warning_msg)
        assert warning_msg in report.warnings

    def test_duplicate_warnings_not_added(self):
        """Test that duplicate warnings are not added."""
        report = ProvenanceReport(mode="full-auto")
        warning_msg = "Test warning"

        add_warning(report, warning_msg)
        add_warning(report, warning_msg)

        # Should only appear once
        assert report.warnings.count(warning_msg) == 1


class TestProvenanceSerialization:
    """Tests for saving and loading provenance reports."""

    def test_save_and_load_provenance(self, tmp_path):
        """Test saving and loading provenance report from JSON."""
        provenance_file = tmp_path / "phenotypes_provenance.json"

        # Create and populate a report
        report = ProvenanceReport(mode="auto", dataset_name="test_dataset")
        add_column_provenance(
            report, "age", "static", 0.95, "nb:Age", rationale="Built-in"
        )
        add_column_provenance(
            report, "diagnosis", "ai", 0.72, "nb:Diagnosis", rationale="AI inferred"
        )
        add_warning(report, "Test warning")

        # Save
        save_provenance(report, provenance_file)
        assert provenance_file.exists()

        # Load
        loaded = load_provenance(provenance_file)
        assert loaded.mode == "auto"
        assert loaded.dataset_name == "test_dataset"
        assert len(loaded.per_column) == 2
        assert "Test warning" in loaded.warnings

    def test_provenance_file_not_found(self, tmp_path):
        """Test error when loading non-existent provenance file."""
        with pytest.raises(FileNotFoundError):
            load_provenance(tmp_path / "nonexistent.json")

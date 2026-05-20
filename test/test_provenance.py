from datetime import datetime

import pytest

from npdb.report.provenance import (
    ColumnProvenance,
    ProvenanceReport,
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
            rationale="Found in built-in dictionary",
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
            ai_model_version="1.2",
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
            ai_threshold=0.5,
        )
        assert report.mode == "full-auto"
        assert report.ai_provider == "ollama"
        assert report.ai_threshold == 0.5


class TestAddColumnProvenance:
    """Tests for adding column provenance to report."""

    def test_add_static_column(self):
        """Test adding a static mapping to report."""
        report = ProvenanceReport(mode="auto")
        report.add_column_provenance(
            column_name="age",
            source="static",
            confidence=0.95,
            variable="nb:Age",
            rationale="Built-in mapping",
        )

        assert "age" in report.per_column
        assert report.per_column["age"].source == "static"
        assert report.mapping_source_counts["static"] == 1

    def test_add_ai_column_with_threshold(self):
        """Test adding AI mapping and verifying confidence distribution."""
        report = ProvenanceReport(mode="full-auto")
        report.add_column_provenance(
            column_name="diagnosis",
            source="ai",
            confidence=0.72,
            variable="nb:Diagnosis",
            rationale="AI inference",
            ai_model="ollama/neural-chat",
        )

        assert report.mapping_source_counts["ai"] == 1
        # Confidence 0.72 should be in "medium" bucket [0.7, 0.84]
        assert 0.72 in report.confidence_distribution.medium

    def test_add_multiple_columns_and_count_sources(self):
        """Test adding multiple columns and verifying source counts."""
        report = ProvenanceReport(mode="auto")

        report.add_column_provenance(
            "id", "static", 1.0, "nb:ParticipantID", rationale="Static"
        )
        report.add_column_provenance(
            "age", "deterministic", 0.82, "nb:Age", rationale="Fuzzy"
        )
        report.add_column_provenance("diag", "ai", 0.65, "nb:Diagnosis", rationale="AI")

        assert report.mapping_source_counts["static"] == 1
        assert report.mapping_source_counts["deterministic"] == 1
        assert report.mapping_source_counts["ai"] == 1


class TestAddWarning:
    """Tests for adding warnings to report."""

    def test_add_warning(self):
        """Test adding a warning message."""
        report = ProvenanceReport(mode="full-auto")
        warning_msg = "Low confidence threshold used in full-auto mode"

        report.add_warning(warning_msg)
        assert warning_msg in report.warnings

    def test_duplicate_warnings_not_added(self):
        """Test that duplicate warnings are not added."""
        report = ProvenanceReport(mode="full-auto")
        warning_msg = "Test warning"

        report.add_warning(warning_msg)
        report.add_warning(warning_msg)

        # Should only appear once
        assert report.warnings.count(warning_msg) == 1


class TestProvenanceSerialization:
    """Tests for saving and loading provenance reports."""

    def test_save_and_load_provenance(self, tmp_path):
        """Test saving and loading provenance report from JSON."""
        provenance_file = tmp_path / "phenotypes_provenance.json"

        # Create and populate a report
        report = ProvenanceReport(mode="auto", dataset_name="test_dataset")
        report.add_column_provenance(
            "age", "static", 0.95, "nb:Age", rationale="Built-in"
        )
        report.add_column_provenance(
            "diagnosis", "ai", 0.72, "nb:Diagnosis", rationale="AI inferred"
        )
        report.add_warning("Test warning")

        # Save
        report.save(provenance_file)
        assert provenance_file.exists()

        # Load
        loaded = ProvenanceReport.from_file(provenance_file)
        assert loaded.mode == "auto"
        assert loaded.dataset_name == "test_dataset"
        assert len(loaded.per_column) == 2
        assert "Test warning" in loaded.warnings

    def test_provenance_file_not_found(self, tmp_path):
        """Test error when loading non-existent provenance file."""
        with pytest.raises(FileNotFoundError):
            ProvenanceReport.from_file(tmp_path / "nonexistent.json")


# ---------------------------------------------------------------------------
# Observer isolation and LedgerObserver tests
# ---------------------------------------------------------------------------


class TestObserverIsolation:
    """Extra observers must not corrupt the ProvenanceReport."""

    def _make_resolved_mapping(self, col: str):
        from npdb.automation.mappings.resolvers import ResolvedMapping

        return ResolvedMapping(
            column_name=col,
            mapped_variable="nb:Age",
            source="static",
            confidence=0.95,
            rationale="test",
            mapping_data={},
        )

    def test_extra_observer_does_not_duplicate_provenance(self):
        """Registering a no-op extra observer must not double-write provenance."""
        from npdb.report.observers import ProvenanceObserver

        report = ProvenanceReport(mode="manual")
        obs = ProvenanceObserver(report)
        mapping = self._make_resolved_mapping("age")

        # Simulate two observers but only ProvenanceObserver writes provenance
        obs.on_resolved("age", mapping)

        class NoopObserver:
            def on_resolved(self, col, m):
                pass

            def on_warning(self, msg):
                pass

        noop = NoopObserver()
        noop.on_resolved("age", mapping)  # must not add a second entry

        assert len(report.per_column) == 1

    def test_warning_observer_isolation(self):
        """Warnings from extra observers must not leak into unrelated ProvenanceReports."""
        from npdb.report.observers import ProvenanceObserver

        report_a = ProvenanceReport(mode="auto")
        report_b = ProvenanceReport(mode="auto")
        obs_a = ProvenanceObserver(report_a)
        obs_b = ProvenanceObserver(report_b)

        obs_a.on_warning("warn-for-a")

        assert "warn-for-a" in report_a.warnings
        assert "warn-for-a" not in report_b.warnings


class TestLedgerObserver:
    """LedgerObserver forwards warnings to RunLedger but does not write provenance."""

    def test_warning_forwarded_to_ledger(self):
        from npdb.report import LedgerObserver, RunLedger

        ledger = RunLedger()
        obs = LedgerObserver(ledger)
        obs.on_warning("column 'foo' below confidence threshold")

        assert len(ledger.warnings) == 1
        assert "foo" in ledger.warnings[0]

    def test_on_resolved_is_noop(self):
        """on_resolved must not raise and must not modify the ledger."""
        from npdb.automation.mappings.resolvers import ResolvedMapping
        from npdb.report import LedgerObserver, RunLedger

        ledger = RunLedger()
        obs = LedgerObserver(ledger)
        mapping = ResolvedMapping(
            column_name="sex",
            mapped_variable="nb:Sex",
            source="static",
            confidence=1.0,
            rationale="exact match",
            mapping_data={},
        )
        obs.on_resolved("sex", mapping)

        assert ledger.warnings == []
        assert ledger.errors == []

    def test_multiple_warnings_accumulated(self):
        from npdb.report import LedgerObserver, RunLedger

        ledger = RunLedger()
        obs = LedgerObserver(ledger)
        for i in range(5):
            obs.on_warning(f"warning {i}")

        assert len(ledger.warnings) == 5

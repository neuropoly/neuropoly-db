"""
Tests for TSV file parsing utilities.
"""

import pytest
import tempfile
from pathlib import Path

from npdb.utils import (
    get_unique_values,
    parse_tsv_columns
)


@pytest.fixture
def sample_tsv() -> Path:
    """Create a sample TSV file for testing."""
    content = """participant_id\tage\tsex\tdiagnosis\tcognitive_score
sub-01\t22\tM\tCTRL\t95.5
sub-02\t28\tF\tPD\t78.2
sub-03\t35\tM\tCTRL\t92.1
sub-04\t31\tF\tPD\t85.0
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
        f.write(content)
        return Path(f.name)


@pytest.fixture
def empty_tsv() -> Path:
    """Create an empty TSV file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
        return Path(f.name)


class TestParseTSVColumns:
    """Tests for parse_tsv_columns function."""

    def test_parse_valid_columns(self, sample_tsv: Path):
        """Test parsing valid column headers."""
        columns = parse_tsv_columns(sample_tsv)

        assert len(columns) == 5
        assert columns[0] == "participant_id"
        assert columns[1] == "age"
        assert columns[2] == "sex"
        assert columns[3] == "diagnosis"
        assert columns[4] == "cognitive_score"

    def test_file_not_found(self):
        """Test error when file doesn't exist."""
        with pytest.raises(FileNotFoundError):
            parse_tsv_columns(Path("/nonexistent/file.tsv"))

    def test_empty_file(self, empty_tsv: Path):
        """Test error when file is empty."""
        with pytest.raises(ValueError, match="empty or has no header"):
            parse_tsv_columns(empty_tsv)

    def test_column_order_preserved(self, sample_tsv: Path):
        """Test that column order is preserved."""
        columns = parse_tsv_columns(sample_tsv)
        expected = ["participant_id", "age",
                    "sex", "diagnosis", "cognitive_score"]
        assert columns == expected


class TestGetUniqueValues:
    """Tests for get_unique_values function."""

    def test_get_unique_values_categorical(self, sample_tsv: Path):
        """Test extracting unique values from categorical column."""
        unique_values = get_unique_values(sample_tsv, "sex")

        assert len(unique_values) == 2
        assert "M" in unique_values
        assert "F" in unique_values

    def test_get_unique_values_diagnostic(self, sample_tsv: Path):
        """Test extracting unique values from diagnosis column."""
        unique_values = get_unique_values(sample_tsv, "diagnosis")

        assert len(unique_values) == 2
        assert "CTRL" in unique_values
        assert "PD" in unique_values

    def test_get_unique_values_numeric(self, sample_tsv: Path):
        """Test extracting unique values from numeric column."""
        unique_values = get_unique_values(sample_tsv, "age")

        assert len(unique_values) == 4
        assert "22" in unique_values
        assert "28" in unique_values
        assert "35" in unique_values
        assert "31" in unique_values

    def test_column_not_found(self, sample_tsv: Path):
        """Test error when column doesn't exist."""
        with pytest.raises(ValueError, match="Column 'nonexistent' not found"):
            get_unique_values(sample_tsv, "nonexistent")

    def test_file_not_found(self):
        """Test error when file doesn't exist."""
        with pytest.raises(FileNotFoundError):
            get_unique_values(Path("/nonexistent/file.tsv"), "age")


class TestTSVParsingEdgeCases:
    """Tests for edge cases in TSV parsing."""

    def test_single_column(self):
        """Test TSV with only one column."""
        content = "participant_id\nsub-01\nsub-02\n"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
            f.write(content)
            tsv_path = Path(f.name)

        try:
            columns = parse_tsv_columns(tsv_path)
            assert columns == ["participant_id"]
        finally:
            tsv_path.unlink()

    def test_many_columns(self):
        """Test TSV with many columns."""
        headers = "\t".join([f"col_{i}" for i in range(100)])
        row = "\t".join([str(i) for i in range(100)])
        content = f"{headers}\n{row}\n"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
            f.write(content)
            tsv_path = Path(f.name)

        try:
            columns = parse_tsv_columns(tsv_path)
            assert len(columns) == 100
            assert columns[0] == "col_0"
            assert columns[99] == "col_99"
        finally:
            tsv_path.unlink()

    def test_missing_data_in_column(self):
        """Test handling of missing data (NA, n/a, N/A)."""
        content = "participant_id\tage\nsub-01\t25\nsub-02\tNA\nsub-03\tn/a\nsub-04\tN/A\nsub-05\t30\n"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
            f.write(content)
            tsv_path = Path(f.name)

        try:
            unique_values = get_unique_values(tsv_path, "age")
            # Should only contain numeric values, not NA/n/a/N/A
            assert "25" in unique_values
            assert "30" in unique_values
            assert "NA" not in unique_values
            assert "n/a" not in unique_values
            assert "N/A" not in unique_values
        finally:
            tsv_path.unlink()

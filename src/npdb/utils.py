from pathlib import Path
from typing import List, Set


def parse_tsv_columns(tsv_path: Path) -> List[str]:
    """
    Parse column names from a TSV file.

    Reads the first line of a TSV file and returns the tab-separated column headers.

    Args:
        tsv_path: Path to the TSV file (typically participants.tsv).

    Returns:
        List of column names in order.

    Raises:
        FileNotFoundError: If the TSV file doesn't exist.
        ValueError: If the file is empty or has no valid headers.
    """
    if not tsv_path.exists():
        raise FileNotFoundError(f"TSV file not found: {tsv_path}")

    try:
        with open(tsv_path, 'r', encoding='utf-8') as f:
            header_line = f.readline().strip()
    except Exception as e:
        raise ValueError(f"Failed to read TSV file: {e}")

    if not header_line:
        raise ValueError(f"TSV file is empty or has no header: {tsv_path}")

    columns = header_line.split('\t')
    if not columns or all(not c for c in columns):
        raise ValueError(f"No valid columns found in TSV file: {tsv_path}")

    return columns


def get_unique_values(tsv_path: Path, column_name: str) -> Set[str]:
    """
    Extract unique values for a specific column from a TSV file.

    Args:
        tsv_path: Path to the TSV file.
        column_name: Name of the column to extract values from.

    Returns:
        Set of unique values in the column (excluding header and null values).

    Raises:
        FileNotFoundError: If the TSV file doesn't exist.
        ValueError: If the column doesn't exist.
    """
    if not tsv_path.exists():
        raise FileNotFoundError(f"TSV file not found: {tsv_path}")

    columns = parse_tsv_columns(tsv_path)
    if column_name not in columns:
        raise ValueError(
            f"Column '{column_name}' not found in TSV. Available columns: {columns}")

    column_index = columns.index(column_name)
    unique_values: Set[str] = set()

    try:
        with open(tsv_path, 'r', encoding='utf-8') as f:
            # Skip header
            f.readline()
            # Read data rows
            for line in f:
                parts = line.strip().split('\t')
                if len(parts) > column_index:
                    value = parts[column_index]
                    if value and value != 'n/a' and value != 'NA' and value != 'N/A':
                        unique_values.add(value)
    except Exception as e:
        raise ValueError(f"Failed to parse column values: {e}")

    return unique_values

"""
Tests for header translation map feature.

Covers:
- load_header_map: valid/invalid JSON, structure validation
- validate_header_map_keys: valid keys, invalid keys
- apply_header_map: renaming, case-insensitive matching, dry-run,
  ambiguous/conflicting mappings, no-match no-op
"""

import json
from pathlib import Path

import pytest

from npdb.annotation.standardize import (
    apply_header_map,
    load_header_map,
    validate_header_map_keys,
)


# ── Helpers ────────────────────────────────────────────────────────


def _make_tsv(tmp_path: Path, headers: list[str], rows: list[list[str]] | None = None) -> Path:
    """Create a TSV file with given headers and optional data rows."""
    tsv = tmp_path / "participants.tsv"
    lines = ["\t".join(headers)]
    for row in rows or []:
        lines.append("\t".join(row))
    tsv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tsv


def _read_headers(tsv_path: Path) -> list[str]:
    with open(tsv_path, "r", encoding="utf-8") as f:
        return f.readline().rstrip("\n").split("\t")


def _write_header_map(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "header_map.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ── load_header_map tests ─────────────────────────────────────────


class TestLoadHeaderMap:
    def test_valid_file(self, tmp_path):
        hmap = {"age": ["Age_Baseline", "AGE"], "sex": ["Gender"]}
        path = _write_header_map(tmp_path, hmap)
        result = load_header_map(path)
        assert result == hmap

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_header_map(tmp_path / "nonexistent.json")

    def test_invalid_root_type(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(ValueError, match="JSON object"):
            load_header_map(path)

    def test_non_string_value(self, tmp_path):
        path = _write_header_map(tmp_path, {"age": "not_a_list"})
        with pytest.raises(ValueError, match="lists of strings"):
            load_header_map(path)

    def test_non_string_list_items(self, tmp_path):
        path = _write_header_map(tmp_path, {"age": [1, 2]})
        with pytest.raises(ValueError, match="lists of strings"):
            load_header_map(path)

    def test_empty_map(self, tmp_path):
        path = _write_header_map(tmp_path, {})
        result = load_header_map(path)
        assert result == {}


# ── validate_header_map_keys tests ────────────────────────────────


class TestValidateHeaderMapKeys:
    VALID_KEYS = {"participant_id", "age", "sex", "diagnosis", "session_id"}

    def test_valid_keys_pass(self):
        hmap = {"age": ["AGE"], "sex": ["Gender"]}
        validate_header_map_keys(hmap, self.VALID_KEYS)  # no error

    def test_invalid_keys_raise(self):
        hmap = {"age": ["AGE"], "nonexistent": ["foo"]}
        with pytest.raises(ValueError, match="nonexistent"):
            validate_header_map_keys(hmap, self.VALID_KEYS)

    def test_error_lists_all_invalid(self):
        hmap = {"bad1": ["x"], "bad2": ["y"], "age": ["AGE"]}
        with pytest.raises(ValueError, match="bad1.*bad2|bad2.*bad1"):
            validate_header_map_keys(hmap, self.VALID_KEYS)

    def test_empty_map_passes(self):
        validate_header_map_keys({}, self.VALID_KEYS)  # no error


# ── apply_header_map tests ────────────────────────────────────────


class TestApplyHeaderMap:
    def test_basic_rename(self, tmp_path):
        tsv = _make_tsv(tmp_path, ["SubjectID", "Age_Baseline", "notes"],
                        [["sub-01", "25", "ok"]])
        hmap = {"participant_id": ["SubjectID"], "age": ["Age_Baseline"]}
        renames = apply_header_map(tsv, hmap)
        assert renames == {
            "SubjectID": "participant_id", "Age_Baseline": "age"}
        assert _read_headers(tsv) == ["participant_id", "age", "notes"]

    def test_case_insensitive(self, tmp_path):
        tsv = _make_tsv(tmp_path, ["GENDER", "AGE"])
        hmap = {"sex": ["gender"], "age": ["age"]}
        renames = apply_header_map(tsv, hmap)
        assert renames == {"GENDER": "sex", "AGE": "age"}
        assert _read_headers(tsv) == ["sex", "age"]

    def test_no_match_noop(self, tmp_path):
        tsv = _make_tsv(tmp_path, ["participant_id", "age", "sex"])
        hmap = {"diagnosis": ["condition", "disease_status"]}
        renames = apply_header_map(tsv, hmap)
        assert renames == {}
        assert _read_headers(tsv) == ["participant_id", "age", "sex"]

    def test_preserves_data_rows(self, tmp_path):
        tsv = _make_tsv(tmp_path, ["Subject", "Score"],
                        [["sub-01", "10"], ["sub-02", "20"]])
        hmap = {"participant_id": ["Subject"]}
        apply_header_map(tsv, hmap)
        lines = tsv.read_text(encoding="utf-8").strip().split("\n")
        assert lines[0] == "participant_id\tScore"
        assert lines[1] == "sub-01\t10"
        assert lines[2] == "sub-02\t20"

    def test_dry_run_no_write(self, tmp_path, capsys):
        tsv = _make_tsv(tmp_path, ["GENDER", "AGE"])
        hmap = {"sex": ["gender"], "age": ["age"]}
        renames = apply_header_map(tsv, hmap, dry_run=True)
        assert renames == {"GENDER": "sex", "AGE": "age"}
        # Headers NOT changed on disk
        assert _read_headers(tsv) == ["GENDER", "AGE"]
        captured = capsys.readouterr()
        assert "Dry-run" in captured.out

    def test_ambiguous_variant_in_map(self, tmp_path):
        """Same variant listed under two different keys."""
        tsv = _make_tsv(tmp_path, ["Gender"])
        hmap = {"sex": ["Gender"], "other": ["Gender"]}
        with pytest.raises(ValueError, match="Ambiguous"):
            apply_header_map(tsv, hmap)

    def test_conflicting_columns(self, tmp_path):
        """Two TSV columns both map to the same target key."""
        tsv = _make_tsv(tmp_path, ["Gender", "biological_sex"])
        hmap = {"sex": ["Gender", "biological_sex"]}
        with pytest.raises(ValueError, match="Conflicting"):
            apply_header_map(tsv, hmap)

    def test_column_already_named_correctly(self, tmp_path):
        """Column already has the desired name — no rename entry."""
        tsv = _make_tsv(tmp_path, ["age", "sex"])
        hmap = {"age": ["age", "AGE"], "sex": ["sex", "Gender"]}
        renames = apply_header_map(tsv, hmap)
        # Both already match but are identical to target, no rename needed
        assert renames == {}

    def test_empty_tsv(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text("", encoding="utf-8")
        hmap = {"age": ["AGE"]}
        renames = apply_header_map(tsv, hmap)
        assert renames == {}

    def test_multiple_variants_first_match_wins(self, tmp_path):
        """A key has multiple variants; only the one present in TSV is used."""
        tsv = _make_tsv(tmp_path, ["age_at_baseline", "other"])
        hmap = {"age": ["age_years", "age_at_baseline", "AGE"]}
        renames = apply_header_map(tsv, hmap)
        assert renames == {"age_at_baseline": "age"}
        assert _read_headers(tsv) == ["age", "other"]

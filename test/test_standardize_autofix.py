"""
Tests for auto-fix functions in npdb.annotation.standardize:
  - fix_single_column_tsv: comma/semicolon/pipe delimiter detection and rewrite
  - dedup_participant_ids: duplicate row removal
  - auto_add_missing_value_sentinels: NA-like and whitespace variants
  - load_categorical_terms: JSON config loader
  - fix_missing_levels: categorical levels repair, add, and TSV rewrite
"""

import csv
import json
import pytest
from pathlib import Path

from npdb.annotation.standardize import (
    fix_single_column_tsv,
    dedup_participant_ids,
    auto_add_missing_value_sentinels,
    load_categorical_terms,
    fix_missing_levels,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_tsv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _write_annotations(path: Path, col: str, variable_type: str, levels: dict) -> None:
    ann = {
        col: {
            "Annotations": {
                "IsAbout": {"TermURL": "nb:SomeVar"},
                "VariableType": variable_type,
                "Levels": levels,
                "MissingValues": [],
            }
        }
    }
    path.write_text(json.dumps(ann, indent=2), encoding="utf-8")


# ===========================================================================
# fix_single_column_tsv
# ===========================================================================

class TestFixSingleColumnTsv:
    """Tests for fix_single_column_tsv()."""

    # ── Already tab-separated → no change ─────────────────────────────────

    def test_no_op_when_already_tab_separated(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        content = "participant_id\tage\tsex\nsub-01\t25\tM\nsub-02\t30\tF\n"
        tsv.write_text(content, encoding="utf-8")

        result = fix_single_column_tsv(tsv)

        assert result == []
        assert tsv.read_text(encoding="utf-8") == content

    # ── Comma delimiter ────────────────────────────────────────────────────

    def test_comma_delimited_rewritten_as_tabs(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text(
            "participant_id,age,sex\nsub-01,25,M\nsub-02,30,F\n",
            encoding="utf-8",
        )

        result = fix_single_column_tsv(tsv)

        assert result  # warning returned
        rows = _read_tsv(tsv)
        assert rows[0]["participant_id"] == "sub-01"
        assert rows[0]["age"] == "25"
        assert rows[1]["sex"] == "F"

    def test_comma_warning_mentions_delimiter(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text("participant_id,age\nsub-01,25\n", encoding="utf-8")
        warnings = fix_single_column_tsv(tsv)
        assert any("," in w for w in warnings)

    # ── Semicolon delimiter ────────────────────────────────────────────────

    def test_semicolon_delimited_rewritten_as_tabs(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text(
            "participant_id;age;sex\nsub-01;25;M\nsub-02;30;F\n",
            encoding="utf-8",
        )

        result = fix_single_column_tsv(tsv)

        assert result
        rows = _read_tsv(tsv)
        assert rows[0]["age"] == "25"

    def test_semicolon_warning_mentions_delimiter(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text("participant_id;age\nsub-01;25\n", encoding="utf-8")
        warnings = fix_single_column_tsv(tsv)
        assert any(";" in w for w in warnings)

    # ── Pipe delimiter ─────────────────────────────────────────────────────

    def test_pipe_delimited_rewritten_as_tabs(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text(
            "participant_id|age|sex\nsub-01|25|M\n",
            encoding="utf-8",
        )

        result = fix_single_column_tsv(tsv)

        assert result
        rows = _read_tsv(tsv)
        assert rows[0]["age"] == "25"

    # ── Single-column (genuine) file → no change ──────────────────────────

    def test_single_column_no_delimiter_no_op(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text("participant_id\nsub-01\nsub-02\n", encoding="utf-8")

        result = fix_single_column_tsv(tsv)

        assert result == []

    # ── Edge cases ─────────────────────────────────────────────────────────

    def test_empty_file_no_op(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text("", encoding="utf-8")
        assert fix_single_column_tsv(tsv) == []

    def test_nonexistent_file_returns_empty(self, tmp_path):
        assert fix_single_column_tsv(tmp_path / "ghost.tsv") == []

    def test_all_data_rows_rewritten(self, tmp_path):
        """Every data row (not just header) must have delimiters replaced."""
        tsv = tmp_path / "participants.tsv"
        tsv.write_text(
            "participant_id,age\nsub-01,25\nsub-02,30\nsub-03,35\n",
            encoding="utf-8",
        )
        fix_single_column_tsv(tsv)
        rows = _read_tsv(tsv)
        assert len(rows) == 3
        ages = [r["age"] for r in rows]
        assert ages == ["25", "30", "35"]

    def test_rewrite_preserves_values_with_spaces(self, tmp_path):
        """Values containing spaces (but not the delimiter) survive unchanged."""
        tsv = tmp_path / "participants.tsv"
        tsv.write_text(
            "participant_id,notes\nsub-01,all good\nsub-02,needs review\n",
            encoding="utf-8",
        )
        fix_single_column_tsv(tsv)
        rows = _read_tsv(tsv)
        assert rows[0]["notes"] == "all good"
        assert rows[1]["notes"] == "needs review"

    def test_returns_exactly_one_warning(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
        warnings = fix_single_column_tsv(tsv)
        assert len(warnings) == 1


# ===========================================================================
# dedup_participant_ids
# ===========================================================================

class TestDedupParticipantIds:
    """Tests for dedup_participant_ids()."""

    # ── No duplicates → no change ──────────────────────────────────────────

    def test_no_op_when_all_unique(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text(
            "participant_id\tage\nsub-01\t25\nsub-02\t30\n",
            encoding="utf-8",
        )
        result = dedup_participant_ids(tsv)
        assert result == []
        assert len(_read_tsv(tsv)) == 2

    # ── Single duplicate removed ───────────────────────────────────────────

    def test_single_duplicate_removed(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text(
            "participant_id\tage\nsub-01\t25\nsub-01\t26\nsub-02\t30\n",
            encoding="utf-8",
        )

        warnings = dedup_participant_ids(tsv)

        rows = _read_tsv(tsv)
        ids = [r["participant_id"] for r in rows]
        assert ids == ["sub-01", "sub-02"]
        assert len(warnings) == 1
        assert "sub-01" in warnings[0]

    # ── First occurrence kept ──────────────────────────────────────────────

    def test_first_occurrence_kept_not_second(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text(
            "participant_id\tage\nsub-01\t25\nsub-01\t99\n",
            encoding="utf-8",
        )
        dedup_participant_ids(tsv)
        rows = _read_tsv(tsv)
        assert rows[0]["age"] == "25"  # first row kept

    # ── Multiple duplicates ────────────────────────────────────────────────

    def test_multiple_duplicates_all_removed(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        rows_in = (
            "participant_id\tage\n"
            "sub-01\t25\n"
            "sub-02\t30\n"
            "sub-01\t26\n"
            "sub-03\t35\n"
            "sub-02\t31\n"
        )
        tsv.write_text(rows_in, encoding="utf-8")

        warnings = dedup_participant_ids(tsv)

        rows = _read_tsv(tsv)
        ids = [r["participant_id"] for r in rows]
        assert ids == ["sub-01", "sub-02", "sub-03"]
        assert len(warnings) == 2

    # ── Warning messages contain the dropped IDs ──────────────────────────

    def test_warning_contains_dropped_id(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text(
            "participant_id\tage\nsub-99\t20\nsub-99\t21\n",
            encoding="utf-8",
        )
        warnings = dedup_participant_ids(tsv)
        assert any("sub-99" in w for w in warnings)

    # ── No participant_id column → no-op ──────────────────────────────────

    def test_no_op_when_no_participant_id_column(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text("subject\tage\nsub-01\t25\nsub-01\t26\n",
                       encoding="utf-8")
        result = dedup_participant_ids(tsv)
        assert result == []
        # File unchanged
        assert len(_read_tsv(tsv)) == 2

    # ── Edge cases ─────────────────────────────────────────────────────────

    def test_empty_file_returns_empty(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text("", encoding="utf-8")
        assert dedup_participant_ids(tsv) == []

    def test_nonexistent_file_returns_empty(self, tmp_path):
        assert dedup_participant_ids(tmp_path / "ghost.tsv") == []

    def test_header_row_not_duplicated(self, tmp_path):
        """Header must appear exactly once after dedup."""
        tsv = tmp_path / "participants.tsv"
        tsv.write_text(
            "participant_id\tage\nsub-01\t25\nsub-01\t26\n",
            encoding="utf-8",
        )
        dedup_participant_ids(tsv)
        lines = tsv.read_text(encoding="utf-8").splitlines()
        header_count = sum(1 for l in lines if l.startswith("participant_id"))
        assert header_count == 1

    def test_all_columns_preserved_after_dedup(self, tmp_path):
        """Other columns are not dropped or reordered."""
        tsv = tmp_path / "participants.tsv"
        tsv.write_text(
            "participant_id\tage\tsex\ndiag\t25\tM\ndiag\t26\tF\n",
            encoding="utf-8",
        )
        dedup_participant_ids(tsv)
        rows = _read_tsv(tsv)
        assert "age" in rows[0]
        assert "sex" in rows[0]


# ===========================================================================
# auto_add_missing_value_sentinels
# ===========================================================================

class TestAutoAddMissingValueSentinels:
    """Tests for auto_add_missing_value_sentinels()."""

    # ── NA-like values added to MissingValues ──────────────────────────────

    @pytest.mark.parametrize("na_value", [
        "n/a", "N/A", "na", "NA", "", "-", "unknown", "?",
    ])
    def test_na_like_value_added(self, tmp_path, na_value):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        with open(tsv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["participant_id", "sex"], delimiter="\t")
            writer.writeheader()
            writer.writerow({"participant_id": "sub-01", "sex": "M"})
            writer.writerow({"participant_id": "sub-02", "sex": na_value})
        _write_annotations(ann, "sex", "Categorical", {"M": {}, "F": {}})

        warnings = auto_add_missing_value_sentinels(tsv, ann)

        data = json.loads(ann.read_text())
        missing = data["sex"]["Annotations"]["MissingValues"]
        assert na_value in missing, f"{na_value!r} should be in MissingValues"

    # ── Known level values not re-added ───────────────────────────────────

    def test_existing_level_not_added_to_missing(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        with open(tsv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["participant_id", "sex"], delimiter="\t")
            writer.writeheader()
            writer.writerow({"participant_id": "sub-01", "sex": "M"})
            writer.writerow({"participant_id": "sub-02", "sex": "F"})
        _write_annotations(ann, "sex", "Categorical", {"M": {}, "F": {}})

        warnings = auto_add_missing_value_sentinels(tsv, ann)

        data = json.loads(ann.read_text())
        missing = data["sex"]["Annotations"]["MissingValues"]
        assert "M" not in missing
        assert "F" not in missing

    # ── Whitespace variant of a known level ────────────────────────────────

    def test_whitespace_variant_added_to_missing(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        with open(tsv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["participant_id", "sex"], delimiter="\t")
            writer.writeheader()
            writer.writerow({"participant_id": "sub-01", "sex": "M"})
            # trailing space
            writer.writerow({"participant_id": "sub-02", "sex": "F "})
        _write_annotations(ann, "sex", "Categorical", {"M": {}, "F": {}})

        warnings = auto_add_missing_value_sentinels(tsv, ann)

        data = json.loads(ann.read_text())
        missing = data["sex"]["Annotations"]["MissingValues"]
        assert "F " in missing

    # ── Non-NA unrecognized values left untouched ──────────────────────────

    def test_unrecognized_non_na_value_not_added(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        with open(tsv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["participant_id", "sex"], delimiter="\t")
            writer.writeheader()
            writer.writerow({"participant_id": "sub-01", "sex": "M"})
            # unknown, not NA
            writer.writerow({"participant_id": "sub-02", "sex": "X"})
        _write_annotations(ann, "sex", "Categorical", {"M": {}, "F": {}})

        auto_add_missing_value_sentinels(tsv, ann)

        data = json.loads(ann.read_text())
        missing = data["sex"]["Annotations"]["MissingValues"]
        assert "X" not in missing

    # ── Continuous columns ignored ─────────────────────────────────────────

    def test_continuous_column_not_modified(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        with open(tsv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["participant_id", "age"], delimiter="\t")
            writer.writeheader()
            writer.writerow({"participant_id": "sub-01", "age": "n/a"})
        _write_annotations(ann, "age", "Continuous", {})

        auto_add_missing_value_sentinels(tsv, ann)

        data = json.loads(ann.read_text())
        # Continuous columns are skipped
        missing = data["age"]["Annotations"].get("MissingValues", [])
        assert missing == []

    # ── Missing file guards ────────────────────────────────────────────────

    def test_returns_empty_when_annotations_missing(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text("participant_id\tage\nsub-01\t25\n", encoding="utf-8")
        result = auto_add_missing_value_sentinels(tsv, tmp_path / "nope.json")
        assert result == []

    def test_returns_empty_when_tsv_missing(self, tmp_path):
        ann = tmp_path / "annotations.json"
        _write_annotations(ann, "sex", "Categorical", {"M": {}})
        result = auto_add_missing_value_sentinels(tmp_path / "nope.tsv", ann)
        assert result == []


# ===========================================================================
# load_categorical_terms
# ===========================================================================

class TestLoadCategoricalTerms:
    """Tests for load_categorical_terms()."""

    def _write_config(self, path: Path, data: dict) -> None:
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_valid_config_builds_correct_dicts(self, tmp_path):
        cfg = tmp_path / "terms.json"
        self._write_config(cfg, {
            "hc": {
                "TermURL": "ncit:C94342",
                "Label": "Healthy Control",
                "aliases": ["control", "ctrl"],
            },
            "pd": {
                "TermURL": "snomed:49049000",
                "Label": "Parkinson's disease",
                "aliases": [],
            },
        })

        alias_to_preferred, preferred_to_term = load_categorical_terms(cfg)

        # preferred key maps to itself
        assert alias_to_preferred["hc"] == "hc"
        assert alias_to_preferred["pd"] == "pd"
        # aliases map to preferred
        assert alias_to_preferred["control"] == "hc"
        assert alias_to_preferred["ctrl"] == "hc"
        # preferred_to_term carries TermURL and Label
        assert preferred_to_term["hc"] == {
            "TermURL": "ncit:C94342", "Label": "Healthy Control"}
        assert preferred_to_term["pd"]["TermURL"] == "snomed:49049000"

    def test_unknown_alias_returns_none_from_lookup(self, tmp_path):
        cfg = tmp_path / "terms.json"
        self._write_config(cfg, {
            "hc": {"TermURL": "ncit:C94342", "Label": "Healthy Control", "aliases": []},
        })
        alias_to_preferred, _ = load_categorical_terms(cfg)
        assert alias_to_preferred.get("nonexistent") is None

    def test_missing_term_url_raises_value_error(self, tmp_path):
        cfg = tmp_path / "terms.json"
        self._write_config(cfg, {
            "hc": {"Label": "Healthy Control", "aliases": []},
        })
        with pytest.raises(ValueError, match="TermURL"):
            load_categorical_terms(cfg)

    def test_missing_label_raises_value_error(self, tmp_path):
        cfg = tmp_path / "terms.json"
        self._write_config(cfg, {
            "hc": {"TermURL": "ncit:C94342", "aliases": []},
        })
        with pytest.raises(ValueError, match="Label"):
            load_categorical_terms(cfg)

    def test_missing_aliases_raises_value_error(self, tmp_path):
        cfg = tmp_path / "terms.json"
        self._write_config(cfg, {
            "hc": {"TermURL": "ncit:C94342", "Label": "Healthy Control"},
        })
        with pytest.raises(ValueError, match="aliases"):
            load_categorical_terms(cfg)

    def test_non_list_aliases_raises_value_error(self, tmp_path):
        cfg = tmp_path / "terms.json"
        self._write_config(cfg, {
            "hc": {"TermURL": "ncit:C94342", "Label": "Healthy Control", "aliases": "control"},
        })
        with pytest.raises(ValueError, match="aliases"):
            load_categorical_terms(cfg)


# ===========================================================================
# fix_missing_levels
# ===========================================================================

def _write_categorical_annotations(path: Path, col: str, levels: dict) -> None:
    """Write an annotations file with a single Categorical column."""
    ann = {
        col: {
            "Annotations": {
                "IsAbout": {"TermURL": "nb:Diagnosis"},
                "VariableType": "Categorical",
                "Levels": levels,
                "MissingValues": [],
            }
        }
    }
    path.write_text(json.dumps(ann, indent=2), encoding="utf-8")


def _write_tsv(path: Path, col: str, values: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["participant_id", col], delimiter="\t")
        writer.writeheader()
        for i, v in enumerate(values, start=1):
            writer.writerow({"participant_id": f"sub-{i:02d}", col: v})


class TestFixMissingLevels:
    """Tests for fix_missing_levels()."""

    # ── Alias → TSV rewrite and preferred key in Levels ───────────────────

    def test_alias_tsv_value_rewritten_to_preferred(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        _write_tsv(tsv, "diagnosis", ["control"])
        _write_categorical_annotations(ann, "diagnosis", {})

        fix_missing_levels(tsv, ann)

        rows = _read_tsv(tsv)
        assert rows[0]["diagnosis"] == "hc"

    def test_alias_levels_key_is_preferred_term(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        _write_tsv(tsv, "diagnosis", ["control"])
        _write_categorical_annotations(ann, "diagnosis", {})

        fix_missing_levels(tsv, ann)

        data = json.loads(ann.read_text())
        levels = data["diagnosis"]["Annotations"]["Levels"]
        assert "hc" in levels
        assert "control" not in levels
        assert levels["hc"]["TermURL"] == "ncit:C94342"

    def test_already_preferred_no_rename_correct_levels(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        _write_tsv(tsv, "diagnosis", ["hc"])
        _write_categorical_annotations(ann, "diagnosis", {})

        fix_missing_levels(tsv, ann)

        rows = _read_tsv(tsv)
        assert rows[0]["diagnosis"] == "hc"  # unchanged

        data = json.loads(ann.read_text())
        levels = data["diagnosis"]["Annotations"]["Levels"]
        assert "hc" in levels
        assert levels["hc"]["TermURL"] == "ncit:C94342"

    def test_unknown_value_gets_unresolved_no_tsv_rename(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        _write_tsv(tsv, "diagnosis", ["mystery"])
        _write_categorical_annotations(ann, "diagnosis", {})

        fix_missing_levels(tsv, ann)

        rows = _read_tsv(tsv)
        assert rows[0]["diagnosis"] == "mystery"  # not renamed

        data = json.loads(ann.read_text())
        levels = data["diagnosis"]["Annotations"]["Levels"]
        assert "mystery" in levels
        assert levels["mystery"]["TermURL"] == "nb:Unresolved"

    # ── Repair path: existing entries without TermURL ──────────────────────

    def test_repair_existing_invalid_entry_with_alias(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        _write_tsv(tsv, "diagnosis", ["control"])
        # Pre-populate levels with an invalid entry (no TermURL)
        _write_categorical_annotations(
            ann, "diagnosis", {"control": {"Description": "bad"}})

        warnings = fix_missing_levels(tsv, ann)

        assert any("repaired" in w for w in warnings)
        data = json.loads(ann.read_text())
        levels = data["diagnosis"]["Annotations"]["Levels"]
        assert "hc" in levels
        assert levels["hc"]["TermURL"] == "ncit:C94342"

    # ── Warning emitted for renamed TSV values ─────────────────────────────

    def test_warning_emitted_for_tsv_rename(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        _write_tsv(tsv, "diagnosis", ["ctrl"])
        _write_categorical_annotations(ann, "diagnosis", {})

        warnings = fix_missing_levels(tsv, ann)

        assert any("renamed" in w and "ctrl" in w for w in warnings)

    # ── Missing file guards ────────────────────────────────────────────────

    def test_returns_empty_when_annotations_missing(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        tsv.write_text(
            "participant_id\tdiagnosis\nsub-01\thc\n", encoding="utf-8")
        result = fix_missing_levels(tsv, tmp_path / "nope.json")
        assert result == []

    def test_returns_empty_when_tsv_missing(self, tmp_path):
        ann = tmp_path / "annotations.json"
        _write_categorical_annotations(ann, "diagnosis", {})
        result = fix_missing_levels(tmp_path / "nope.tsv", ann)
        assert result == []

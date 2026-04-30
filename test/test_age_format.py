"""
Tests for age format detection and fix_age_format().

Covers every notation that the system accepts:
  Standard:      89+   18-25   P30Y   42,5   25.5   31
  Non-standard:  +89   42-     -42   (all map to nb:FromBounded, emit warning)

Also covers:
  - NA-saturation soft warning (≥90 % missing)
  - Multiple columns; only the nb:Age-annotated one is touched
  - In-place annotation file update
  - No-op when format already matches
  - Missing TSV / missing annotations file → graceful empty return
"""

import csv
import json
import pytest
from pathlib import Path

from npdb.annotation.standardize import (
    _detect_age_format,
    _AGE_FORMAT_PATTERNS,
    _AGE_NONSTANDARD_PATTERNS,
    fix_age_format,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_tsv(path: Path, rows: list[dict]) -> None:
    """Write a minimal tab-separated file."""
    if not rows:
        path.write_text("")
        return
    headers = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _write_annotations(path: Path, age_col: str, fmt_iri: str = "nb:FromFloat") -> None:
    """Write a minimal neurobagel annotations JSON with one age column."""
    ann = {
        age_col: {
            "Annotations": {
                "IsAbout": {"TermURL": "nb:Age", "Label": "Age"},
                "VariableType": "Continuous",
                "Format": {"TermURL": fmt_iri, "Label": fmt_iri},
                "MissingValues": [],
            }
        }
    }
    path.write_text(json.dumps(ann, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# _detect_age_format — unit tests for every accepted notation
# ---------------------------------------------------------------------------

class TestDetectAgeFormat:
    """Unit tests for the internal _detect_age_format helper."""

    # ── Standard canonical notations ──────────────────────────────────────

    def test_standard_bounded_trailing_plus(self):
        assert _detect_age_format(["89+", "75+", "62+"]) == "nb:FromBounded"

    def test_standard_bounded_float_trailing_plus(self):
        assert _detect_age_format(["89.5+", "75.0+"]) == "nb:FromBounded"

    def test_standard_range_two_sided(self):
        assert _detect_age_format(["18-25", "26-35", "36-45"]) == "nb:FromRange"

    def test_standard_range_float(self):
        assert _detect_age_format(["18.5-25.5"]) == "nb:FromRange"

    def test_standard_iso8601(self):
        assert _detect_age_format(["P30Y", "P45Y6M", "P22Y3M"]) == "nb:FromISO8601"

    def test_standard_euro_decimal_comma(self):
        assert _detect_age_format(["42,5", "31,0", "28,9"]) == "nb:FromEuro"

    def test_standard_plain_integer_falls_back_to_float(self):
        assert _detect_age_format(["22", "31", "45"]) == "nb:FromFloat"

    def test_standard_plain_decimal_falls_back_to_float(self):
        assert _detect_age_format(["22.5", "31.0"]) == "nb:FromFloat"

    # ── Non-standard accepted notations ────────────────────────────────────

    def test_nonstandard_leading_plus(self):
        """'+89' is non-standard but maps to nb:FromBounded."""
        assert _detect_age_format(["+89", "+75", "+62"]) == "nb:FromBounded"

    def test_nonstandard_leading_plus_float(self):
        assert _detect_age_format(["+89.5"]) == "nb:FromBounded"

    def test_nonstandard_trailing_minus(self):
        """'42-' (upper-bound only) maps to nb:FromBounded."""
        assert _detect_age_format(["42-", "55-", "60-"]) == "nb:FromBounded"

    def test_nonstandard_trailing_minus_float(self):
        assert _detect_age_format(["42.5-"]) == "nb:FromBounded"

    def test_nonstandard_leading_minus(self):
        """'-42' (upper-bound only, negative prefix) maps to nb:FromBounded."""
        assert _detect_age_format(["-42", "-55"]) == "nb:FromBounded"

    def test_nonstandard_leading_minus_float(self):
        assert _detect_age_format(["-42.5"]) == "nb:FromBounded"

    # ── Mixed standard + non-standard: majority wins ─────────────────────

    def test_mixed_standard_nonstandard_majority_bounded(self):
        """Standard '89+' and non-standard '+89' both vote for FromBounded."""
        result = _detect_age_format(["89+", "+75", "62+", "+50"])
        assert result == "nb:FromBounded"

    def test_mixed_formats_majority_wins(self):
        """If most values are range, detect range even with some outliers."""
        result = _detect_age_format(["18-25", "26-35", "36-45", "46-55", "P10Y"])
        assert result == "nb:FromRange"

    # ── Edge cases ─────────────────────────────────────────────────────────

    def test_empty_list_returns_float(self):
        assert _detect_age_format([]) == "nb:FromFloat"

    def test_whitespace_only_values_ignored(self):
        assert _detect_age_format(["  ", "\t", ""]) == "nb:FromFloat"

    def test_values_with_surrounding_whitespace_stripped(self):
        """Leading/trailing whitespace is stripped before pattern match."""
        assert _detect_age_format([" 89+ ", " 62+ "]) == "nb:FromBounded"

    def test_single_value(self):
        assert _detect_age_format(["P25Y"]) == "nb:FromISO8601"

    # ── Pattern registry completeness ─────────────────────────────────────

    def test_all_standard_patterns_are_in_registry(self):
        """Every expected term is represented in _AGE_FORMAT_PATTERNS."""
        terms = {term for term, _ in _AGE_FORMAT_PATTERNS}
        assert "nb:FromBounded" in terms
        assert "nb:FromRange" in terms
        assert "nb:FromISO8601" in terms
        assert "nb:FromEuro" in terms
        assert "nb:FromFloat" not in terms  # fallback, not in registry

    def test_nonstandard_patterns_subset_of_registry(self):
        """Every non-standard pattern must also appear in the main registry."""
        registry_patterns = [pat.pattern for _, pat in _AGE_FORMAT_PATTERNS]
        for _, ns_pat in _AGE_NONSTANDARD_PATTERNS:
            assert ns_pat.pattern in registry_patterns, (
                f"Non-standard pattern {ns_pat.pattern!r} is not in _AGE_FORMAT_PATTERNS"
            )

    def test_standard_bounded_takes_priority_over_nonstandard(self):
        """'89+' (standard) matches before '+89' (non-standard) in the ordered list."""
        # Both produce FromBounded so the term is the same — verify by checking
        # that the match comes from the standard pattern position.
        standard_pat = _AGE_FORMAT_PATTERNS[0][1]  # first entry: nb:FromBounded, "^\d+..."
        assert standard_pat.match("89+") is not None
        assert standard_pat.match("+89") is None  # standard pattern does NOT match +89


# ---------------------------------------------------------------------------
# fix_age_format — integration tests (TSV + annotations file)
# ---------------------------------------------------------------------------

class TestFixAgeFormat:
    """Integration tests for fix_age_format()."""

    # ── Standard notations update annotations correctly ───────────────────

    @pytest.mark.parametrize("values,expected_fmt", [
        (["89+", "75+", "62+"], "nb:FromBounded"),
        (["18-25", "26-35"], "nb:FromRange"),
        (["P30Y", "P45Y"], "nb:FromISO8601"),
        (["42,5", "31,0"], "nb:FromEuro"),
        (["22", "31", "45"], "nb:FromFloat"),
    ])
    def test_standard_format_detected_and_written(
        self, tmp_path, values, expected_fmt
    ):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "participants_annotations.json"
        _write_tsv(tsv, [{"participant_id": f"sub-0{i+1}", "age": v}
                          for i, v in enumerate(values)])
        _write_annotations(ann, "age", fmt_iri="nb:FromFloat")

        warnings = fix_age_format(tsv, ann)

        data = json.loads(ann.read_text())
        actual = data["age"]["Annotations"]["Format"]["TermURL"]
        assert actual == expected_fmt, (
            f"Expected {expected_fmt!r}, got {actual!r} for values {values}"
        )

    # ── Non-standard notations: accepted + soft warning ───────────────────

    @pytest.mark.parametrize("values,label", [
        (["+89", "+75"], "leading plus"),
        (["42-", "55-"], "trailing minus"),
        (["-42", "-55"], "leading minus"),
        (["+89", "42-", "-55"], "mixed non-standard"),
    ])
    def test_nonstandard_notation_accepted_and_maps_to_bounded(
        self, tmp_path, values, label
    ):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "participants_annotations.json"
        _write_tsv(tsv, [{"participant_id": f"sub-0{i+1}", "age": v}
                          for i, v in enumerate(values)])
        _write_annotations(ann, "age", fmt_iri="nb:FromFloat")

        warnings = fix_age_format(tsv, ann)

        # Format must be updated to FromBounded
        data = json.loads(ann.read_text())
        assert data["age"]["Annotations"]["Format"]["TermURL"] == "nb:FromBounded", (
            f"[{label}] Expected nb:FromBounded"
        )
        # Must emit a non-standard warning
        nonstandard_warns = [w for w in warnings if "non-standard" in w]
        assert nonstandard_warns, f"[{label}] Expected a non-standard notation warning"

    def test_nonstandard_warning_contains_examples(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "participants_annotations.json"
        _write_tsv(tsv, [{"participant_id": "sub-01", "age": "+42"},
                          {"participant_id": "sub-02", "age": "+55"}])
        _write_annotations(ann, "age", fmt_iri="nb:FromFloat")

        warnings = fix_age_format(tsv, ann)
        warn_text = " ".join(warnings)
        assert "+42" in warn_text or "+55" in warn_text

    def test_nonstandard_warning_suggests_canonical_form(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "participants_annotations.json"
        _write_tsv(tsv, [{"participant_id": "sub-01", "age": "+89"}])
        _write_annotations(ann, "age", fmt_iri="nb:FromFloat")

        warnings = fix_age_format(tsv, ann)
        warn_text = " ".join(warnings)
        # Should mention the canonical form "89+"
        assert "89+" in warn_text

    # ── No update when format already matches ─────────────────────────────

    def test_no_op_when_format_already_correct(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "participants_annotations.json"
        _write_tsv(tsv, [{"participant_id": "sub-01", "age": "89+"},
                          {"participant_id": "sub-02", "age": "75+"}])
        _write_annotations(ann, "age", fmt_iri="nb:FromBounded")

        original_mtime = ann.stat().st_mtime
        warnings = fix_age_format(tsv, ann)

        # No format-change warning
        fmt_warns = [w for w in warnings if "updated Format" in w]
        assert not fmt_warns

    # ── Only nb:Age column is updated ─────────────────────────────────────

    def test_only_age_annotated_column_touched(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "participants_annotations.json"
        _write_tsv(tsv, [
            {"participant_id": "sub-01", "age": "89+", "age2": "50"},
            {"participant_id": "sub-02", "age": "75+", "age2": "30"},
        ])
        # Two columns but only 'age' annotated as nb:Age
        annotations = {
            "age": {
                "Annotations": {
                    "IsAbout": {"TermURL": "nb:Age"},
                    "Format": {"TermURL": "nb:FromFloat", "Label": "nb:FromFloat"},
                    "MissingValues": [],
                }
            },
            "age2": {
                "Annotations": {
                    "IsAbout": {"TermURL": "nb:SomeOtherVar"},
                    "Format": {"TermURL": "nb:FromFloat", "Label": "nb:FromFloat"},
                    "MissingValues": [],
                }
            },
        }
        ann.write_text(json.dumps(annotations), encoding="utf-8")

        fix_age_format(tsv, ann)

        data = json.loads(ann.read_text())
        assert data["age"]["Annotations"]["Format"]["TermURL"] == "nb:FromBounded"
        # age2 must be untouched
        assert data["age2"]["Annotations"]["Format"]["TermURL"] == "nb:FromFloat"

    # ── NA-saturation warning ──────────────────────────────────────────────

    def test_na_saturation_warning_when_90_percent_missing(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "participants_annotations.json"
        rows = [{"participant_id": f"sub-{i:02d}", "age": "n/a"} for i in range(9)]
        rows.append({"participant_id": "sub-10", "age": "25"})
        _write_tsv(tsv, rows)
        _write_annotations(ann, "age")

        warnings = fix_age_format(tsv, ann)
        na_warns = [w for w in warnings if "missing/NA" in w or "≥90" in w]
        assert na_warns

    def test_no_na_saturation_warning_below_threshold(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "participants_annotations.json"
        rows = [{"participant_id": f"sub-{i:02d}", "age": "n/a"} for i in range(5)]
        rows += [{"participant_id": f"sub-{i+5:02d}", "age": str(20 + i)}
                 for i in range(5)]
        _write_tsv(tsv, rows)
        _write_annotations(ann, "age")

        warnings = fix_age_format(tsv, ann)
        na_warns = [w for w in warnings if "missing/NA" in w or "≥90" in w]
        assert not na_warns

    # ── Graceful no-ops ────────────────────────────────────────────────────

    def test_returns_empty_when_annotations_missing(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "nonexistent_annotations.json"
        _write_tsv(tsv, [{"participant_id": "sub-01", "age": "25"}])
        result = fix_age_format(tsv, ann)
        assert result == []

    def test_returns_empty_when_tsv_missing(self, tmp_path):
        tsv = tmp_path / "nonexistent.tsv"
        ann = tmp_path / "annotations.json"
        _write_annotations(ann, "age")
        result = fix_age_format(tsv, ann)
        assert result == []

    def test_returns_empty_when_no_age_column_annotated(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        _write_tsv(tsv, [{"participant_id": "sub-01", "score": "42"}])
        annotations = {
            "score": {
                "Annotations": {
                    "IsAbout": {"TermURL": "nb:Assessment"},
                    "Format": {"TermURL": "nb:FromFloat"},
                    "MissingValues": [],
                }
            }
        }
        ann.write_text(json.dumps(annotations), encoding="utf-8")
        result = fix_age_format(tsv, ann)
        assert result == []

    def test_all_na_values_returns_without_format_change(self, tmp_path):
        """All-NA age column: NA warning emitted but no format-change warning."""
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        _write_tsv(tsv, [
            {"participant_id": "sub-01", "age": "n/a"},
            {"participant_id": "sub-02", "age": "N/A"},
            {"participant_id": "sub-03", "age": ""},
        ])
        _write_annotations(ann, "age", fmt_iri="nb:FromFloat")

        warnings = fix_age_format(tsv, ann)
        fmt_warns = [w for w in warnings if "updated Format" in w]
        assert not fmt_warns

    # ── Annotation file written atomically ────────────────────────────────

    def test_annotation_file_updated_in_place(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        _write_tsv(tsv, [{"participant_id": "sub-01", "age": "P25Y"}])
        _write_annotations(ann, "age", fmt_iri="nb:FromFloat")

        fix_age_format(tsv, ann)

        data = json.loads(ann.read_text())
        assert data["age"]["Annotations"]["Format"]["TermURL"] == "nb:FromISO8601"

    # ── Exact update warning message content ─────────────────────────────

    def test_update_warning_mentions_old_and_new_format(self, tmp_path):
        tsv = tmp_path / "participants.tsv"
        ann = tmp_path / "annotations.json"
        _write_tsv(tsv, [{"participant_id": f"sub-{i:02d}", "age": f"P{20+i}Y"}
                          for i in range(5)])
        _write_annotations(ann, "age", fmt_iri="nb:FromFloat")

        warnings = fix_age_format(tsv, ann)
        update_warns = [w for w in warnings if "updated Format.TermURL" in w]
        assert update_warns
        assert "nb:FromFloat" in update_warns[0]
        assert "nb:FromISO8601" in update_warns[0]

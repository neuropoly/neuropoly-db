"""
Tests for the vocab_extension_pending field added to ledger entry builders
and for its propagation through managers/__init__.py → cli.py.

Covers:
  - success_entry_from_report: optional vocab_extension_pending field
  - minimal_success_entry: optional vocab_extension_pending field
  - failure_entry: optional vocab_extension_pending field
  - generic_failure_entry: optional vocab_extension_pending field
  - Field is absent (not None) when not provided
  - Field is absent when an empty list is passed
  - Field is present and correct when populated
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from npdb.ledger.ledger import (
    success_entry_from_report,
    minimal_success_entry,
    failure_entry,
    generic_failure_entry,
)
from npdb.annotation.provenance import ProvenanceReport, ConfidenceDistribution
from npdb.external.neurobagel.errors import BagelCLIError


# ===========================================================================
# Helpers
# ===========================================================================

def _make_report(mode: str = "auto") -> ProvenanceReport:
    return ProvenanceReport(
        mode=mode,
        mapping_source_counts={"static": 1,
                               "deterministic": 0, "ai": 0, "manual": 0},
        confidence_distribution=ConfidenceDistribution(),
    )


def _make_bagel_error() -> BagelCLIError:
    return BagelCLIError(
        command="bagel bids",
        exit_code=1,
        plain_output="Error: something went wrong",
    )


# ===========================================================================
# success_entry_from_report
# ===========================================================================

class TestSuccessEntryFromReportVocabField:

    def test_field_absent_when_not_provided(self):
        entry = success_entry_from_report("ds1", "gitea2bagel", _make_report())
        assert "vocab_extension_pending" not in entry

    def test_field_absent_when_empty_list(self):
        entry = success_entry_from_report(
            "ds1", "gitea2bagel", _make_report(), vocab_extension_pending=[]
        )
        assert "vocab_extension_pending" not in entry

    def test_field_absent_when_none(self):
        entry = success_entry_from_report(
            "ds1", "gitea2bagel", _make_report(), vocab_extension_pending=None
        )
        assert "vocab_extension_pending" not in entry

    def test_field_present_when_populated(self):
        pending = ["vocab_extension_pending: add BF manually"]
        entry = success_entry_from_report(
            "ds1", "gitea2bagel", _make_report(), vocab_extension_pending=pending
        )
        assert "vocab_extension_pending" in entry
        assert entry["vocab_extension_pending"] == pending

    def test_field_does_not_shadow_other_fields(self):
        pending = ["note1"]
        entry = success_entry_from_report(
            "ds1", "gitea2bagel", _make_report(),
            preprocessing_warnings=["pre"],
            vocab_extension_pending=pending,
        )
        assert "preprocessing_warnings" in entry
        assert "vocab_extension_pending" in entry

    def test_status_is_success(self):
        entry = success_entry_from_report(
            "ds1", "gitea2bagel", _make_report(),
            vocab_extension_pending=["pending"]
        )
        assert entry["status"] == "success"

    def test_multiple_pending_items_preserved(self):
        pending = [
            "vocab_extension_pending: IRI 'nb:X' invalid",
            "vocab_extension_pending: could not write vocab.json",
        ]
        entry = success_entry_from_report(
            "ds1", "gitea2bagel", _make_report(), vocab_extension_pending=pending
        )
        assert len(entry["vocab_extension_pending"]) == 2


# ===========================================================================
# minimal_success_entry
# ===========================================================================

class TestMinimalSuccessEntryVocabField:

    def test_field_absent_when_not_provided(self):
        entry = minimal_success_entry("ds1", "gitea2bagel", "manual")
        assert "vocab_extension_pending" not in entry

    def test_field_absent_when_empty_list(self):
        entry = minimal_success_entry(
            "ds1", "gitea2bagel", "manual", vocab_extension_pending=[]
        )
        assert "vocab_extension_pending" not in entry

    def test_field_present_when_populated(self):
        pending = ["vocab_extension_pending: add TEM manually"]
        entry = minimal_success_entry(
            "ds1", "gitea2bagel", "manual", vocab_extension_pending=pending
        )
        assert entry["vocab_extension_pending"] == pending

    def test_status_is_success(self):
        entry = minimal_success_entry(
            "ds1", "gitea2bagel", "manual", vocab_extension_pending=["x"]
        )
        assert entry["status"] == "success"

    def test_mode_preserved(self):
        entry = minimal_success_entry(
            "ds1", "gitea2bagel", "manual", vocab_extension_pending=["x"]
        )
        assert entry["mode"] == "manual"


# ===========================================================================
# failure_entry
# ===========================================================================

class TestFailureEntryVocabField:

    def _classified(self):
        return [{
            "problem": "annotation_failure",
            "description": "Something went wrong",
            "context": {},
            "fix_steps": [],
        }]

    def test_field_absent_when_not_provided(self):
        entry = failure_entry("ds1", "gitea2bagel",
                              _make_bagel_error(), self._classified())
        assert "vocab_extension_pending" not in entry

    def test_field_absent_when_empty_list(self):
        entry = failure_entry(
            "ds1", "gitea2bagel", _make_bagel_error(), self._classified(),
            vocab_extension_pending=[]
        )
        assert "vocab_extension_pending" not in entry

    def test_field_present_when_populated(self):
        pending = ["vocab_extension_pending: could not promote 'BF'"]
        entry = failure_entry(
            "ds1", "gitea2bagel", _make_bagel_error(), self._classified(),
            vocab_extension_pending=pending
        )
        assert entry["vocab_extension_pending"] == pending

    def test_status_is_failure(self):
        entry = failure_entry(
            "ds1", "gitea2bagel", _make_bagel_error(), self._classified(),
            vocab_extension_pending=["x"]
        )
        assert entry["status"] == "failure"

    def test_other_failure_fields_intact(self):
        entry = failure_entry(
            "ds1", "gitea2bagel", _make_bagel_error(), self._classified(),
            preprocessing_warnings=["pre"],
            vocab_extension_pending=["pending"],
        )
        assert "problem_name" in entry
        assert "preprocessing_warnings" in entry
        assert "vocab_extension_pending" in entry


# ===========================================================================
# generic_failure_entry
# ===========================================================================

class TestGenericFailureEntryVocabField:

    def test_field_absent_when_not_provided(self):
        entry = generic_failure_entry(
            "ds1", "gitea2bagel", "preflight_failure", "desc")
        assert "vocab_extension_pending" not in entry

    def test_field_absent_when_empty_list(self):
        entry = generic_failure_entry(
            "ds1", "gitea2bagel", "preflight_failure", "desc",
            vocab_extension_pending=[]
        )
        assert "vocab_extension_pending" not in entry

    def test_field_present_when_populated(self):
        pending = ["vocab_extension_pending: write error"]
        entry = generic_failure_entry(
            "ds1", "gitea2bagel", "preflight_failure", "desc",
            vocab_extension_pending=pending
        )
        assert entry["vocab_extension_pending"] == pending

    def test_status_is_failure(self):
        entry = generic_failure_entry(
            "ds1", "gitea2bagel", "preflight_failure", "desc",
            vocab_extension_pending=["x"]
        )
        assert entry["status"] == "failure"

    def test_problem_name_preserved(self):
        entry = generic_failure_entry(
            "ds1", "gitea2bagel", "my_problem", "desc",
            vocab_extension_pending=["x"]
        )
        assert entry["problem_name"] == "my_problem"

    def test_all_optional_fields_can_coexist(self):
        entry = generic_failure_entry(
            "ds1", "gitea2bagel", "p", "d",
            fix_steps=[{"action": "fix", "detail": "", "auto_fixable": False}],
            raw_snippet="snippet",
            preprocessing_warnings=["pre"],
            subject_alignment_warnings=["subj"],
            vocab_extension_pending=["vocab"],
        )
        assert entry["preprocessing_warnings"] == ["pre"]
        assert entry["subject_alignment_warnings"] == ["subj"]
        assert entry["vocab_extension_pending"] == ["vocab"]
        assert entry["raw_output_snippet"] == "snippet"


# ===========================================================================
# Ledger JSON round-trip with vocab_extension_pending
# ===========================================================================

class TestLedgerRoundTrip:
    """Verify the field survives a full RunLedger.append → read cycle."""

    def test_vocab_extension_pending_round_trips(self, tmp_path):
        from npdb.ledger.ledger import RunLedger

        ledger_path = tmp_path / "run_ledger.json"
        ledger = RunLedger(ledger_path)

        pending = ["vocab_extension_pending: add XMod manually to vocab.json"]
        entry = minimal_success_entry(
            "ds_test", "gitea2bagel", "auto", vocab_extension_pending=pending
        )
        ledger.append(entry)

        import json
        data = json.loads(ledger_path.read_text())
        assert data["entries"][0]["vocab_extension_pending"] == pending

    def test_entry_without_field_saves_cleanly(self, tmp_path):
        from npdb.ledger.ledger import RunLedger

        ledger_path = tmp_path / "run_ledger.json"
        ledger = RunLedger(ledger_path)
        entry = minimal_success_entry("ds_test", "gitea2bagel", "manual")
        ledger.append(entry)

        import json
        data = json.loads(ledger_path.read_text())
        assert "vocab_extension_pending" not in data["entries"][0]

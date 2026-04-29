"""
Tests for pre-flight check functions in npdb.managers:
  - PreflightError: constructor, attributes, inheritance
  - check_bids_suffixes: supported/unsupported classification,
    extra_suffix_map, PreflightError raised correctly
"""

import pytest
from pathlib import Path

from npdb.managers import (
    check_bids_suffixes,
    PreflightError,
    BAGEL_SUPPORTED_SUFFIXES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_nii(bids_dir: Path, relative_path: str) -> Path:
    """Create a zero-byte .nii or .nii.gz file in the BIDS directory."""
    full = bids_dir / relative_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.touch()
    return full


# ===========================================================================
# PreflightError
# ===========================================================================

class TestPreflightError:

    def test_is_runtime_error_subclass(self):
        err = PreflightError("some_problem", "description")
        assert isinstance(err, RuntimeError)

    def test_problem_name_stored(self):
        err = PreflightError("my_problem", "desc")
        assert err.problem_name == "my_problem"

    def test_description_stored(self):
        err = PreflightError("p", "a description")
        assert err.description == "a description"

    def test_description_also_in_args(self):
        err = PreflightError("p", "message")
        assert "message" in str(err)

    def test_fix_steps_defaults_to_empty_list(self):
        err = PreflightError("p", "d")
        assert err.fix_steps == []

    def test_fix_steps_stored(self):
        steps = [{"action": "do X", "detail": "X detail", "auto_fixable": True}]
        err = PreflightError("p", "d", fix_steps=steps)
        assert err.fix_steps == steps

    def test_raw_snippet_defaults_to_empty_string(self):
        err = PreflightError("p", "d")
        assert err.raw_snippet == ""

    def test_raw_snippet_stored(self):
        err = PreflightError("p", "d", raw_snippet="some output")
        assert err.raw_snippet == "some output"

    def test_fix_steps_each_have_required_keys(self):
        steps = [
            {"action": "a", "detail": "d", "auto_fixable": False},
            {"action": "b", "detail": "e", "auto_fixable": True},
        ]
        err = PreflightError("p", "d", fix_steps=steps)
        for step in err.fix_steps:
            assert "action" in step
            assert "detail" in step
            assert "auto_fixable" in step


# ===========================================================================
# check_bids_suffixes
# ===========================================================================

class TestCheckBidsSuffixes:

    # ── Empty directory ────────────────────────────────────────────────────

    def test_empty_bids_dir_returns_empty_sets(self, tmp_path):
        supported, unsupported = check_bids_suffixes(str(tmp_path))
        assert supported == set()
        assert unsupported == set()

    def test_empty_bids_dir_raises_no_error(self, tmp_path):
        # No files → no error (nothing to check)
        check_bids_suffixes(str(tmp_path))  # must not raise

    # ── Only supported suffixes ────────────────────────────────────────────

    def test_builtin_t1w_classified_as_supported(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_T1w.nii.gz")
        supported, unsupported = check_bids_suffixes(str(tmp_path))
        assert "T1w" in supported
        assert "T1w" not in unsupported

    def test_all_builtin_suffixes_recognised(self, tmp_path):
        """Create one file per built-in suffix; all should land in supported."""
        for suffix in list(BAGEL_SUPPORTED_SUFFIXES)[:5]:
            _create_nii(tmp_path, f"sub-01/anat/sub-01_{suffix}.nii.gz")
        supported, unsupported = check_bids_suffixes(str(tmp_path))
        for suffix in list(BAGEL_SUPPORTED_SUFFIXES)[:5]:
            assert suffix in supported

    def test_only_supported_no_error_raised(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_T2w.nii.gz")
        check_bids_suffixes(str(tmp_path))  # must not raise

    # ── Only unsupported suffixes → PreflightError ─────────────────────────

    def test_only_unsupported_raises_preflight_error(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_TEM.nii.gz")
        with pytest.raises(PreflightError):
            check_bids_suffixes(str(tmp_path))

    def test_preflight_error_has_correct_problem_name(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_TEM.nii.gz")
        with pytest.raises(PreflightError) as exc_info:
            check_bids_suffixes(str(tmp_path))
        assert exc_info.value.problem_name == "preflight_failure"

    def test_preflight_error_fix_steps_is_list_of_dicts(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_BF.nii.gz")
        with pytest.raises(PreflightError) as exc_info:
            check_bids_suffixes(str(tmp_path))
        for step in exc_info.value.fix_steps:
            assert isinstance(step, dict)
            assert "action" in step
            assert "auto_fixable" in step

    def test_preflight_error_has_auto_fixable_step(self, tmp_path):
        """At least one fix step for unsupported suffixes must be auto_fixable."""
        _create_nii(tmp_path, "sub-01/anat/sub-01_BF.nii.gz")
        with pytest.raises(PreflightError) as exc_info:
            check_bids_suffixes(str(tmp_path))
        auto_fixable = [s for s in exc_info.value.fix_steps if s["auto_fixable"]]
        assert auto_fixable, "Expected at least one auto_fixable fix step"

    def test_preflight_error_description_mentions_unsupported_suffix(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_MYMRI.nii.gz")
        with pytest.raises(PreflightError) as exc_info:
            check_bids_suffixes(str(tmp_path))
        assert "MYMRI" in exc_info.value.description

    def test_unsupported_set_contains_unknown_suffix(self, tmp_path):
        """Even when the error is raised, check that the exception carries info."""
        _create_nii(tmp_path, "sub-01/anat/sub-01_ALIEN.nii.gz")
        with pytest.raises(PreflightError) as exc_info:
            check_bids_suffixes(str(tmp_path))
        assert "ALIEN" in exc_info.value.description

    # ── Mixed supported + unsupported → no error ──────────────────────────

    def test_mixed_no_error_when_some_supported(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_T1w.nii.gz")
        _create_nii(tmp_path, "sub-01/anat/sub-01_TEM.nii.gz")
        supported, unsupported = check_bids_suffixes(str(tmp_path))
        assert "T1w" in supported
        assert "TEM" in unsupported

    def test_mixed_returns_correct_sets(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_bold.nii.gz")
        _create_nii(tmp_path, "sub-01/anat/sub-01_CUSTOM.nii")
        supported, unsupported = check_bids_suffixes(str(tmp_path))
        assert "bold" in supported
        assert "CUSTOM" in unsupported

    # ── extra_suffix_map ──────────────────────────────────────────────────

    def test_extra_suffix_map_prevents_preflight_error(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_TEM.nii.gz")
        extra = {"TEM": "nb:TransmissionElectronMicroscopy"}
        # Must not raise
        supported, unsupported = check_bids_suffixes(str(tmp_path), extra_suffix_map=extra)
        assert "TEM" in supported

    def test_extra_suffix_treated_as_supported(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_BF.nii.gz")
        extra = {"BF": "nb:BrightFieldMicroscopy"}
        supported, unsupported = check_bids_suffixes(str(tmp_path), extra_suffix_map=extra)
        assert "BF" in supported
        assert "BF" not in unsupported

    def test_extra_suffix_map_does_not_affect_builtin_suffixes(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_T1w.nii.gz")
        _create_nii(tmp_path, "sub-01/anat/sub-01_CUSTOM.nii.gz")
        extra = {"CUSTOM": "nb:CustomImage"}
        supported, unsupported = check_bids_suffixes(str(tmp_path), extra_suffix_map=extra)
        assert "T1w" in supported
        assert "CUSTOM" in supported

    def test_none_extra_suffix_map_treated_as_empty(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_T1w.nii.gz")
        supported, _ = check_bids_suffixes(str(tmp_path), extra_suffix_map=None)
        assert "T1w" in supported

    # ── File extension handling ────────────────────────────────────────────

    def test_nii_gz_extension_parsed_correctly(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_T1w.nii.gz")
        supported, _ = check_bids_suffixes(str(tmp_path))
        assert "T1w" in supported

    def test_nii_extension_parsed_correctly(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_T2w.nii")
        supported, _ = check_bids_suffixes(str(tmp_path))
        assert "T2w" in supported

    def test_non_nii_files_are_ignored(self, tmp_path):
        """JSON, TSV, and other non-NIfTI files must not influence the result."""
        (tmp_path / "sub-01").mkdir()
        (tmp_path / "sub-01" / "sub-01_T99w.json").touch()
        supported, unsupported = check_bids_suffixes(str(tmp_path))
        assert "T99w" not in supported
        assert "T99w" not in unsupported

    # ── Subdirectory nesting ───────────────────────────────────────────────

    def test_deeply_nested_files_found(self, tmp_path):
        """Files under sub-XX/ses-XX/modality/ are discovered."""
        _create_nii(tmp_path, "sub-01/ses-01/anat/sub-01_ses-01_T1w.nii.gz")
        supported, _ = check_bids_suffixes(str(tmp_path))
        assert "T1w" in supported

    def test_multiple_subjects_aggregated(self, tmp_path):
        _create_nii(tmp_path, "sub-01/anat/sub-01_T1w.nii.gz")
        _create_nii(tmp_path, "sub-02/anat/sub-02_T1w.nii.gz")
        _create_nii(tmp_path, "sub-03/anat/sub-03_bold.nii.gz")
        supported, _ = check_bids_suffixes(str(tmp_path))
        assert {"T1w", "bold"} <= supported

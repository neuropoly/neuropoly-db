"""
Tests for src/npdb/external/neurobagel/imaging_extensions.py

Covers:
  - load_extensions / save_extensions
  - STATIC_FALLBACKS: every well-known suffix maps to the correct IRI
  - _NIDM_ALIASES: nidm: entries present and correct
  - _NB_FALLBACKS: nb: entries present and correct
  - load_neuropoly_vocab: basic contract (detailed tests in test_neuropoly_vocab.py)
  - resolve_suffix: cached → vocab file → nidm aliases → llm → generic_fallback chain
  - patch_bagel_suffix_map: injects extra entries into bagel's mapping
  - build_extra_mapping: resolves a list, saves new entries, returns warnings
"""

import json
import re
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from npdb.external.neurobagel.imaging_extensions import (
    STATIC_FALLBACKS,
    _NIDM_ALIASES,
    _NB_FALLBACKS,
    load_extensions,
    load_neuropoly_vocab,
    save_extensions,
    resolve_suffix,
    patch_bagel_suffix_map,
    build_extra_mapping,
    _sanitize_iri,
    _llm_resolve,
)


# ---------------------------------------------------------------------------
# load_extensions / save_extensions
# ---------------------------------------------------------------------------

class TestLoadSaveExtensions:

    def test_load_returns_empty_structure_when_file_missing(self, tmp_path):
        data = load_extensions(tmp_path / "nonexistent.json")
        assert data == {"version": "1", "extensions": {}}

    def test_load_returns_file_contents(self, tmp_path):
        cfg = tmp_path / "ext.json"
        payload = {"version": "1", "extensions": {
            "UNIT1": {"iri": "nidm:T1Weighted"}}}
        cfg.write_text(json.dumps(payload), encoding="utf-8")
        assert load_extensions(cfg) == payload

    def test_save_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "subdir" / "deep" / "ext.json"
        save_extensions({"version": "1", "extensions": {}}, target)
        assert target.exists()

    def test_save_round_trips_data(self, tmp_path):
        cfg = tmp_path / "ext.json"
        data = {"version": "1", "extensions": {"TEM": {"iri": "nb:TEM"}}}
        save_extensions(data, cfg)
        assert load_extensions(cfg) == data

    def test_save_uses_tmp_then_rename(self, tmp_path):
        """No .tmp file should linger after a successful save."""
        cfg = tmp_path / "ext.json"
        save_extensions({"version": "1", "extensions": {}}, cfg)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# STATIC_FALLBACKS
# ---------------------------------------------------------------------------

class TestStaticFallbacks:
    """Verify every well-known suffix maps to the right term."""

    @pytest.mark.parametrize("suffix,expected_iri", [
        ("UNIT1",    "nidm:T1Weighted"),
        ("MP2RAGE",  "nidm:T1Weighted"),
        ("T1map",    "nidm:T1Weighted"),
        ("T2map",    "nidm:T2Weighted"),
        ("T2starmap", "nidm:T2StarWeighted"),
        ("T2star",   "nb:T2StarWeighted"),
        ("SWI",      "nidm:T2StarWeighted"),
        ("BF",       "nb:BrightFieldMicroscopy"),
        ("DF",       "nb:DarkFieldMicroscopy"),
        ("PC",       "nb:PhaseContrastMicroscopy"),
        ("DIC",      "nb:DifferentialInterferenceContrastMicroscopy"),
        ("FLUO",     "nb:FluorescenceMicroscopy"),
        ("CONF",     "nb:ConfocalMicroscopy"),
        ("PLI",      "nb:PolarisedLightImaging"),
        ("TEM",      "nb:TransmissionElectronMicroscopy"),
        ("SEM",      "nb:ScanningElectronMicroscopy"),
        ("uCT",      "nb:MicroComputedTomography"),
        ("OCT",      "nb:OpticalCoherenceTomography"),
        ("CARS",     "nb:CoherentAntiStokesRamanSpectroscopyMicroscopy"),
    ])
    def test_known_suffix_iri(self, suffix, expected_iri):
        assert suffix in STATIC_FALLBACKS, f"'{suffix}' missing from STATIC_FALLBACKS"
        iri, _ = STATIC_FALLBACKS[suffix]
        assert iri == expected_iri

    def test_every_entry_has_description(self):
        for suffix, (iri, desc) in STATIC_FALLBACKS.items():
            assert desc.strip(), f"Empty description for suffix '{suffix}'"

    def test_all_iris_are_valid_prefixed_iris(self):
        """Every IRI must match the namespace:Term pattern."""
        pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*:[a-zA-Z][a-zA-Z0-9_]+$")
        for suffix, (iri, _) in STATIC_FALLBACKS.items():
            assert pattern.match(iri), (
                f"IRI '{iri}' for suffix '{suffix}' is not a valid prefixed IRI"
            )


# ---------------------------------------------------------------------------
# _NIDM_ALIASES  (nidm: MRI variants — not in the vocab file)
# ---------------------------------------------------------------------------

class TestNidmAliases:
    """Verify all nidm:-aliased entries are correct and distinct from nb: terms."""

    @pytest.mark.parametrize("suffix,expected_iri", [
        ("UNIT1",    "nidm:T1Weighted"),
        ("MP2RAGE",  "nidm:T1Weighted"),
        ("T1map",    "nidm:T1Weighted"),
        ("T2map",    "nidm:T2Weighted"),
        ("T2starmap", "nidm:T2StarWeighted"),
        ("SWI",      "nidm:T2StarWeighted"),
        ("SWIp",     "nidm:T2StarWeighted"),
        ("angio",    "nidm:T1Weighted"),
    ])
    def test_nidm_alias_correct(self, suffix, expected_iri):
        assert suffix in _NIDM_ALIASES, f"'{suffix}' missing from _NIDM_ALIASES"
        iri, _ = _NIDM_ALIASES[suffix]
        assert iri == expected_iri

    def test_all_nidm_alias_iris_start_with_nidm(self):
        for suffix, (iri, _) in _NIDM_ALIASES.items():
            assert iri.startswith("nidm:"), (
                f"_NIDM_ALIASES entry '{suffix}' has non-nidm IRI: {iri!r}"
            )

    def test_all_nidm_aliases_have_description(self):
        for suffix, (_, desc) in _NIDM_ALIASES.items():
            assert desc.strip(
            ), f"Empty description for _NIDM_ALIASES['{suffix}']"


# ---------------------------------------------------------------------------
# _NB_FALLBACKS  (nb: backward-compat fallbacks)
# ---------------------------------------------------------------------------

class TestNbFallbacks:
    """Verify all nb: backward-compat fallback entries."""

    def test_all_nb_fallback_iris_start_with_nb(self):
        for suffix, (iri, _) in _NB_FALLBACKS.items():
            assert iri.startswith("nb:"), (
                f"_NB_FALLBACKS entry '{suffix}' has non-nb IRI: {iri!r}"
            )

    def test_all_nb_fallbacks_have_description(self):
        for suffix, (_, desc) in _NB_FALLBACKS.items():
            assert desc.strip(
            ), f"Empty description for _NB_FALLBACKS['{suffix}']"

    @pytest.mark.parametrize("suffix", [
        "BF", "DF", "PC", "DIC", "FLUO", "CONF", "PLI", "TEM", "SEM", "uCT", "OCT", "CARS", "T2star",
    ])
    def test_expected_nb_suffix_present(self, suffix):
        assert suffix in _NB_FALLBACKS

    def test_nb_and_nidm_aliases_do_not_overlap(self):
        shared = set(_NB_FALLBACKS.keys()) & set(_NIDM_ALIASES.keys())
        assert shared == set(), f"Keys in both dicts: {shared}"


# ---------------------------------------------------------------------------
# load_neuropoly_vocab (smoke tests; full coverage in test_neuropoly_vocab.py)
# ---------------------------------------------------------------------------

class TestLoadNeuropolyVocabSmoke:

    def test_returns_dict(self, tmp_path):
        result = load_neuropoly_vocab(tmp_path / "missing.json")
        assert isinstance(result, dict)

    def test_returns_empty_on_missing_file(self, tmp_path):
        assert load_neuropoly_vocab(tmp_path / "missing.json") == {}

    def test_loaded_keys_are_abbreviations(self, tmp_path):
        p = tmp_path / "vocab.json"
        p.write_text(json.dumps([{
            "namespace_prefix": "nb",
            "namespace_url": "http://neurobagel.org/vocab/",
            "vocabulary_name": "Test",
            "version": "1.0.0",
            "terms": [
                {"name": "BF", "id": "BrightFieldMicroscopy", "abbreviation": "BF"},
                {"name": "TEM", "id": "TransmissionElectronMicroscopy",
                    "abbreviation": "TEM"},
            ],
        }]), encoding="utf-8")
        result = load_neuropoly_vocab(p)
        assert set(result.keys()) == {"BF", "TEM"}


# ---------------------------------------------------------------------------
# _sanitize_iri
# ---------------------------------------------------------------------------


class TestSanitizeIri:

    @pytest.mark.parametrize("iri", [
        "nidm:T1Weighted",
        "nb:BrightFieldMicroscopy",
        "nb:CustomImage",
        "foo:Bar123",
    ])
    def test_valid_iris_pass_through(self, iri):
        assert _sanitize_iri(iri) == iri

    @pytest.mark.parametrize("bad", [
        "",
        "no-colon",
        "bad colon:Term",
        ":Term",
        "nb:",
        "nidm:has space",
        "123:Term",
    ])
    def test_invalid_iris_return_none(self, bad):
        assert _sanitize_iri(bad) is None

    def test_leading_trailing_whitespace_stripped(self):
        assert _sanitize_iri("  nidm:T1Weighted  ") == "nidm:T1Weighted"


# ---------------------------------------------------------------------------
# resolve_suffix
# ---------------------------------------------------------------------------

class TestResolveSuffix:

    def _empty_ext(self):
        return {"version": "1", "extensions": {}}

    # ── Chain: cached ──────────────────────────────────────────────────────

    def test_cached_entry_returned_immediately(self):
        ext = {
            "version": "1",
            "extensions": {
                "UNIT1": {"iri": "nidm:T1Weighted", "description": "cached"},
            },
        }
        iri, is_new, desc = resolve_suffix("UNIT1", ext)
        assert iri == "nidm:T1Weighted"
        assert is_new is False

    def test_cached_entry_not_added_again(self):
        ext = {
            "version": "1",
            "extensions": {
                "BF": {"iri": "nb:BrightFieldMicroscopy", "description": "cached"},
            },
        }
        before_len = len(ext["extensions"])
        resolve_suffix("BF", ext)
        assert len(ext["extensions"]) == before_len

    # ── Chain: step 2 — neuropoly_vocab file ──────────────────────────────

    def test_vocab_file_used_at_step2(self, tmp_path):
        vocab = tmp_path / "vocab.json"
        vocab.write_text(json.dumps([{
            "namespace_prefix": "nb",
            "namespace_url": "http://neurobagel.org/vocab/",
            "vocabulary_name": "Test",
            "version": "1.0.0",
            "terms": [{"name": "BF", "id": "BrightFieldMicroscopy", "abbreviation": "BF"}],
        }]), encoding="utf-8")
        ext = self._empty_ext()
        iri, is_new, _ = resolve_suffix("BF", ext, neuropoly_vocab_path=vocab)
        assert iri == "nb:BrightFieldMicroscopy"
        assert is_new is True
        assert ext["extensions"]["BF"]["source"] == "neuropoly_vocab"

    def test_vocab_file_step_skipped_when_path_is_none(self):
        """Without vocab path, resolution falls through to _NB_FALLBACKS at step 3b."""
        ext = self._empty_ext()
        iri, _, _ = resolve_suffix("BF", ext, neuropoly_vocab_path=None)
        assert iri == "nb:BrightFieldMicroscopy"  # still resolved via _NB_FALLBACKS

    # ── Chain: step 3 — nidm aliases ─────────────────────────────────────

    def test_nidm_alias_used_when_not_in_vocab_file(self, tmp_path):
        vocab = tmp_path / "empty_vocab.json"
        vocab.write_text("[]", encoding="utf-8")
        ext = self._empty_ext()
        iri, is_new, _ = resolve_suffix(
            "UNIT1", ext, neuropoly_vocab_path=vocab)
        assert iri == "nidm:T1Weighted"
        assert is_new is True

    def test_nidm_alias_source_is_static_fallback(self):
        ext = self._empty_ext()
        resolve_suffix("MP2RAGE", ext)
        assert ext["extensions"]["MP2RAGE"]["source"] == "static_fallback"

    def test_all_nidm_aliases_resolve(self):
        for suffix, (expected_iri, _) in _NIDM_ALIASES.items():
            ext = self._empty_ext()
            iri, _, _ = resolve_suffix(suffix, ext)
            assert iri == expected_iri, f"suffix '{suffix}': expected {expected_iri!r}, got {iri!r}"

    # ── Chain: static fallback (nb: backward compat) ───────────────────────

    def test_static_fallback_used_when_not_cached(self):
        ext = self._empty_ext()
        iri, is_new, desc = resolve_suffix("TEM", ext)
        assert iri == "nb:TransmissionElectronMicroscopy"
        assert is_new is True

    def test_static_fallback_stored_in_extensions(self):
        ext = self._empty_ext()
        resolve_suffix("TEM", ext)
        assert "TEM" in ext["extensions"]
        assert ext["extensions"]["TEM"]["source"] == "static_fallback"

    def test_static_fallback_description_non_empty(self):
        ext = self._empty_ext()
        _, _, desc = resolve_suffix("UNIT1", ext)
        assert desc.strip()

    # ── Chain: LLM ────────────────────────────────────────────────────────

    def test_llm_used_for_unknown_suffix_when_ai_client_provided(self):
        ext = self._empty_ext()
        mock_client = MagicMock()
        mock_client.chat.return_value = (
            '{"iri": "nb:MyCustomModality", "description": "Some modality."}'
        )

        iri, is_new, desc = resolve_suffix("MYMRI", ext, ai_client=mock_client)

        assert iri == "nb:MyCustomModality"
        assert is_new is True
        assert ext["extensions"]["MYMRI"]["source"] == "llm"

    def test_llm_called_with_suffix_in_prompt(self):
        ext = self._empty_ext()
        mock_client = MagicMock()
        mock_client.chat.return_value = (
            '{"iri": "nb:TestMod", "description": "Test."}'
        )
        resolve_suffix("XMOD", ext, ai_client=mock_client)
        call_args = mock_client.chat.call_args[0][0]
        assert "XMOD" in call_args

    def test_llm_invalid_iri_falls_through_to_generic(self):
        """If LLM returns an invalid IRI, the generic fallback must be used."""
        ext = self._empty_ext()
        mock_client = MagicMock()
        mock_client.chat.return_value = '{"iri": "INVALID IRI", "description": "Bad."}'
        iri, is_new, _ = resolve_suffix("WEIRDMOD", ext, ai_client=mock_client)
        # Should fall through to generic: nb:WEIRDMODImage
        assert iri.startswith("nb:")

    def test_llm_exception_falls_through_to_generic(self):
        ext = self._empty_ext()
        mock_client = MagicMock()
        mock_client.chat.side_effect = RuntimeError("LLM offline")
        iri, _, _ = resolve_suffix("BADMOD", ext, ai_client=mock_client)
        assert iri.startswith("nb:")

    # ── Chain: generic fallback ───────────────────────────────────────────

    def test_generic_fallback_when_no_llm(self):
        ext = self._empty_ext()
        iri, is_new, _ = resolve_suffix("XYZMOD", ext)
        assert iri.startswith("nb:")
        assert is_new is True
        assert ext["extensions"]["XYZMOD"]["source"] == "generic_fallback"

    def test_generic_fallback_iri_is_valid_prefixed_iri(self):
        ext = self._empty_ext()
        iri, _, _ = resolve_suffix("ABC123", ext)
        pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*:[a-zA-Z][a-zA-Z0-9_]+$")
        assert pattern.match(
            iri), f"Generic IRI {iri!r} is not a valid prefixed IRI"

    def test_generic_fallback_handles_suffix_starting_with_digit(self):
        """Suffixes like '3DRA' that start with a digit must produce valid IRI."""
        ext = self._empty_ext()
        iri, _, _ = resolve_suffix("3DRA", ext)
        pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*:[a-zA-Z][a-zA-Z0-9_]+$")
        assert pattern.match(iri)

    def test_generic_fallback_handles_suffix_with_special_chars(self):
        ext = self._empty_ext()
        iri, _, _ = resolve_suffix("my-mod", ext)
        pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*:[a-zA-Z][a-zA-Z0-9_]+$")
        assert pattern.match(iri)

    # ── Idempotence ──────────────────────────────────────────────────────

    def test_resolving_same_suffix_twice_is_idempotent(self):
        ext = self._empty_ext()
        iri1, new1, _ = resolve_suffix("TEM", ext)
        iri2, new2, _ = resolve_suffix("TEM", ext)
        assert iri1 == iri2
        assert new1 is True
        assert new2 is False  # second call: already cached


# ---------------------------------------------------------------------------
# _llm_resolve
# ---------------------------------------------------------------------------

class TestLlmResolve:

    def test_returns_none_when_client_is_none(self):
        assert _llm_resolve("XMOD", None) is None

    def test_parses_clean_json_response(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = (
            '{"iri": "nb:SomeModality", "description": "A modality."}'
        )
        result = _llm_resolve("XMOD", mock_client)
        assert result == ("nb:SomeModality", "A modality.")

    def test_parses_json_embedded_in_prose(self):
        """LLM may wrap JSON in surrounding text."""
        mock_client = MagicMock()
        mock_client.chat.return_value = (
            "Sure! Here's the result:\n"
            '{"iri": "nidm:T1Weighted", "description": "T1."}\n'
            "Let me know if you need more."
        )
        result = _llm_resolve("T1variant", mock_client)
        assert result is not None
        assert result[0] == "nidm:T1Weighted"

    def test_returns_none_on_malformed_json(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = "not json at all"
        assert _llm_resolve("XMOD", mock_client) is None

    def test_returns_none_when_iri_invalid(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = '{"iri": "bad iri", "description": "x."}'
        assert _llm_resolve("XMOD", mock_client) is None


# ---------------------------------------------------------------------------
# patch_bagel_suffix_map
# ---------------------------------------------------------------------------

class TestPatchBagelSuffixMap:

    def test_extra_entries_appear_in_mapping(self):
        try:
            from bagel.utilities import bids_utils
        except ImportError:
            pytest.skip("bagel not installed")

        extra = {"TEM": "nb:TransmissionElectronMicroscopy",
                 "BF": "nb:BrightFieldMicroscopy"}
        patch_bagel_suffix_map(extra)
        mapping = bids_utils.get_bids_suffix_to_std_term_mapping()
        assert mapping.get("TEM") == "nb:TransmissionElectronMicroscopy"
        assert mapping.get("BF") == "nb:BrightFieldMicroscopy"

    def test_builtin_entries_not_removed_after_patch(self):
        try:
            from bagel.utilities import bids_utils
        except ImportError:
            pytest.skip("bagel not installed")

        patch_bagel_suffix_map({"MY_CUSTOM": "nb:Custom"})
        mapping = bids_utils.get_bids_suffix_to_std_term_mapping()
        # Standard entries must still be present
        assert "T1w" in mapping
        assert "bold" in mapping

    def test_idempotent_multiple_patches(self):
        try:
            from bagel.utilities import bids_utils
        except ImportError:
            pytest.skip("bagel not installed")

        extra = {"PATCH_TEST": "nb:PatchTest"}
        patch_bagel_suffix_map(extra)
        patch_bagel_suffix_map(extra)
        mapping = bids_utils.get_bids_suffix_to_std_term_mapping()
        assert mapping.get("PATCH_TEST") == "nb:PatchTest"

    def test_no_error_when_bagel_not_installed(self, monkeypatch):
        """patch_bagel_suffix_map must be a no-op if bagel is not importable."""
        import sys
        # Temporarily hide bagel
        saved = sys.modules.pop("bagel", None)
        saved_utils = sys.modules.pop("bagel.utilities", None)
        saved_bids = sys.modules.pop("bagel.utilities.bids_utils", None)
        try:
            patch_bagel_suffix_map({"X": "nb:X"})  # must not raise
        finally:
            if saved is not None:
                sys.modules["bagel"] = saved
            if saved_utils is not None:
                sys.modules["bagel.utilities"] = saved_utils
            if saved_bids is not None:
                sys.modules["bagel.utilities.bids_utils"] = saved_bids


# ---------------------------------------------------------------------------
# build_extra_mapping
# ---------------------------------------------------------------------------

class TestBuildExtraMapping:

    def test_resolves_list_of_suffixes(self, tmp_path):
        cfg = tmp_path / "imaging_extensions.json"
        extra, warnings = build_extra_mapping(["TEM", "BF"], cfg)

        assert "TEM" in extra
        assert "BF" in extra
        assert extra["TEM"] == "nb:TransmissionElectronMicroscopy"
        assert extra["BF"] == "nb:BrightFieldMicroscopy"

    def test_new_entries_saved_to_disk(self, tmp_path):
        cfg = tmp_path / "imaging_extensions.json"
        build_extra_mapping(["UNIT1"], cfg)
        data = load_extensions(cfg)
        assert "UNIT1" in data["extensions"]

    def test_cached_entries_not_resaved(self, tmp_path):
        cfg = tmp_path / "imaging_extensions.json"
        build_extra_mapping(["TEM"], cfg)
        mtime1 = cfg.stat().st_mtime

        # Second call — already cached, file should not be rewritten
        import time
        time.sleep(0.01)
        build_extra_mapping(["TEM"], cfg)
        mtime2 = cfg.stat().st_mtime
        assert mtime1 == mtime2

    def test_warnings_contain_new_mapping_info(self, tmp_path):
        cfg = tmp_path / "imaging_extensions.json"
        _, warnings = build_extra_mapping(["OCT"], cfg)
        assert warnings
        assert any("OCT" in w for w in warnings)
        assert any("nb:OpticalCoherenceTomography" in w for w in warnings)

    def test_empty_list_returns_empty_mapping(self, tmp_path):
        cfg = tmp_path / "imaging_extensions.json"
        extra, warnings = build_extra_mapping([], cfg)
        assert extra == {}
        assert warnings == []

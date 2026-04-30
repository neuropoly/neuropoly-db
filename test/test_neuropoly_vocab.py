"""
Tests for the NeuroPoly imaging vocabulary file and the functions that read/write it.

Covers:
  - config/neuropoly_imaging_modalities.json schema correctness
  - load_neuropoly_vocab(): happy path, missing file, malformed JSON, partial terms
  - _promote_to_neuropoly_vocab(): adds new terms, skips duplicates,
    rejects invalid IRIs, creates file when absent, handles I/O errors
  - resolve_suffix(): vocab-file step (step 2) in the new resolution order
  - build_extra_mapping(): passes neuropoly_vocab_path, promotion side-effects
"""

import json
import re
import pytest
from pathlib import Path
from unittest.mock import patch

from npdb.external.neurobagel.imaging_extensions import (
    load_neuropoly_vocab,
    resolve_suffix,
    build_extra_mapping,
    _promote_to_neuropoly_vocab,
    load_extensions,
)

# Path to the real vocab file in the workspace
_REAL_VOCAB = Path(__file__).resolve(
).parents[1] / "config" / "neuropoly_imaging_modalities.json"


# ===========================================================================
# Helpers
# ===========================================================================

def _make_vocab_file(tmp_path: Path, terms: list, prefix: str = "nb") -> Path:
    """Write a minimal vocab file to *tmp_path* and return its path."""
    p = tmp_path / "neuropoly_imaging_modalities.json"
    p.write_text(
        json.dumps([{
            "namespace_prefix": prefix,
            "namespace_url": "http://neurobagel.org/vocab/",
            "vocabulary_name": "Test vocab",
            "version": "1.0.0",
            "terms": terms,
        }]),
        encoding="utf-8",
    )
    return p


# ===========================================================================
# config/neuropoly_imaging_modalities.json — schema correctness
# ===========================================================================

class TestRealVocabFileSchema:
    """Validate the committed vocab file against the expected schema."""

    def test_file_exists(self):
        assert _REAL_VOCAB.exists(), f"Missing: {_REAL_VOCAB}"

    def test_parses_as_json_array(self):
        blocks = json.loads(_REAL_VOCAB.read_text(encoding="utf-8"))
        assert isinstance(blocks, list)
        assert len(blocks) >= 1

    def test_first_block_has_required_keys(self):
        block = json.loads(_REAL_VOCAB.read_text(encoding="utf-8"))[0]
        for key in ("namespace_prefix", "namespace_url", "vocabulary_name", "version", "terms"):
            assert key in block, f"Missing key '{key}' in first block"

    def test_namespace_prefix_is_nb(self):
        block = json.loads(_REAL_VOCAB.read_text(encoding="utf-8"))[0]
        assert block["namespace_prefix"] == "nb"

    def test_namespace_url_is_neurobagel(self):
        block = json.loads(_REAL_VOCAB.read_text(encoding="utf-8"))[0]
        assert block["namespace_url"] == "http://neurobagel.org/vocab/"

    def test_all_terms_have_required_fields(self):
        block = json.loads(_REAL_VOCAB.read_text(encoding="utf-8"))[0]
        for term in block["terms"]:
            for field in ("name", "id", "abbreviation"):
                assert field in term and term[field], (
                    f"Term {term!r} missing or empty field '{field}'"
                )

    def test_term_ids_are_valid_camelcase(self):
        block = json.loads(_REAL_VOCAB.read_text(encoding="utf-8"))[0]
        pattern = re.compile(r"^[A-Za-z][A-Za-z0-9]+$")
        for term in block["terms"]:
            assert pattern.match(term["id"]), (
                f"Term id '{term['id']}' does not look like CamelCase"
            )

    def test_abbreviations_are_unique(self):
        block = json.loads(_REAL_VOCAB.read_text(encoding="utf-8"))[0]
        abbrevs = [t["abbreviation"] for t in block["terms"]]
        assert len(abbrevs) == len(
            set(abbrevs)), "Duplicate abbreviations found"

    def test_term_ids_are_unique(self):
        block = json.loads(_REAL_VOCAB.read_text(encoding="utf-8"))[0]
        ids = [t["id"] for t in block["terms"]]
        assert len(ids) == len(set(ids)), "Duplicate term IDs found"

    @pytest.mark.parametrize("abbreviation,expected_id", [
        ("BF",     "BrightFieldMicroscopy"),
        ("DF",     "DarkFieldMicroscopy"),
        ("PC",     "PhaseContrastMicroscopy"),
        ("DIC",    "DifferentialInterferenceContrastMicroscopy"),
        ("FLUO",   "FluorescenceMicroscopy"),
        ("CONF",   "ConfocalMicroscopy"),
        ("PLI",    "PolarisedLightImaging"),
        ("TEM",    "TransmissionElectronMicroscopy"),
        ("SEM",    "ScanningElectronMicroscopy"),
        ("uCT",    "MicroComputedTomography"),
        ("OCT",    "OpticalCoherenceTomography"),
        ("CARS",   "CoherentAntiStokesRamanSpectroscopyMicroscopy"),
        ("T2star", "T2StarWeighted"),
    ])
    def test_expected_term_present(self, abbreviation, expected_id):
        block = json.loads(_REAL_VOCAB.read_text(encoding="utf-8"))[0]
        by_abbr = {t["abbreviation"]: t for t in block["terms"]}
        assert abbreviation in by_abbr, f"Abbreviation '{abbreviation}' not in vocab"
        assert by_abbr[abbreviation]["id"] == expected_id

    def test_term_count_is_14(self):
        block = json.loads(_REAL_VOCAB.read_text(encoding="utf-8"))[0]
        assert len(block["terms"]) == 14


# ===========================================================================
# load_neuropoly_vocab
# ===========================================================================

class TestLoadNeuropolyVocab:

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        result = load_neuropoly_vocab(tmp_path / "nonexistent.json")
        assert result == {}

    def test_returns_empty_dict_on_malformed_json(self, tmp_path):
        bad = tmp_path / "vocab.json"
        bad.write_text("NOT JSON", encoding="utf-8")
        assert load_neuropoly_vocab(bad) == {}

    def test_returns_empty_dict_on_empty_array(self, tmp_path):
        empty = tmp_path / "vocab.json"
        empty.write_text("[]", encoding="utf-8")
        assert load_neuropoly_vocab(empty) == {}

    def test_parses_single_block_correctly(self, tmp_path):
        p = _make_vocab_file(tmp_path, [
            {"name": "Bright-field microscopy",
                "id": "BrightFieldMicroscopy", "abbreviation": "BF"},
        ])
        result = load_neuropoly_vocab(p)
        assert "BF" in result
        assert result["BF"] == (
            "nb:BrightFieldMicroscopy", "Bright-field microscopy")

    def test_keys_are_abbreviations(self, tmp_path):
        p = _make_vocab_file(tmp_path, [
            {"name": "Term A", "id": "TermA", "abbreviation": "TA"},
            {"name": "Term B", "id": "TermB", "abbreviation": "TB"},
        ])
        result = load_neuropoly_vocab(p)
        assert set(result.keys()) == {"TA", "TB"}

    def test_iri_uses_namespace_prefix(self, tmp_path):
        p = _make_vocab_file(tmp_path, [
            {"name": "Something", "id": "SomeThing", "abbreviation": "ST"},
        ], prefix="myns")
        result = load_neuropoly_vocab(p)
        assert result["ST"][0] == "myns:SomeThing"

    def test_term_missing_abbreviation_skipped(self, tmp_path):
        p = _make_vocab_file(tmp_path, [
            {"name": "No Abbr", "id": "NoAbbr"},  # no abbreviation key
            {"name": "Has Abbr", "id": "HasAbbr", "abbreviation": "HA"},
        ])
        result = load_neuropoly_vocab(p)
        assert "HA" in result
        assert len(result) == 1

    def test_term_empty_abbreviation_skipped(self, tmp_path):
        p = _make_vocab_file(tmp_path, [
            {"name": "Empty Abbr", "id": "EmptyAbbr", "abbreviation": ""},
        ])
        assert load_neuropoly_vocab(p) == {}

    def test_loads_real_vocab_file_without_error(self):
        result = load_neuropoly_vocab(_REAL_VOCAB)
        assert len(result) == 14

    def test_real_vocab_bf_maps_to_correct_iri(self):
        result = load_neuropoly_vocab(_REAL_VOCAB)
        assert result["BF"][0] == "nb:BrightFieldMicroscopy"

    def test_multiple_blocks_merged(self, tmp_path):
        p = tmp_path / "vocab.json"
        p.write_text(json.dumps([
            {
                "namespace_prefix": "nb",
                "namespace_url": "http://neurobagel.org/vocab/",
                "vocabulary_name": "Block 1",
                "version": "1.0.0",
                "terms": [{"name": "Term A", "id": "TermA", "abbreviation": "TA"}],
            },
            {
                "namespace_prefix": "custom",
                "namespace_url": "http://example.org/",
                "vocabulary_name": "Block 2",
                "version": "1.0.0",
                "terms": [{"name": "Term B", "id": "TermB", "abbreviation": "TB"}],
            },
        ]), encoding="utf-8")
        result = load_neuropoly_vocab(p)
        assert "TA" in result
        assert "TB" in result
        assert result["TA"][0] == "nb:TermA"
        assert result["TB"][0] == "custom:TermB"


# ===========================================================================
# resolve_suffix — vocab-file step (step 2 in the new resolution order)
# ===========================================================================

class TestResolveSuffixVocabStep:

    def _empty_ext(self):
        return {"version": "1", "extensions": {}}

    def test_vocab_file_used_before_nidm_aliases(self, tmp_path):
        """If vocab file has an entry for a suffix, it wins over _NIDM_ALIASES."""
        # Fabricate a vocab that maps T1w → nb:CustomT1 (unusual but verifies priority)
        p = _make_vocab_file(tmp_path, [
            {"name": "Custom T1", "id": "CustomT1", "abbreviation": "T1w"},
        ])
        ext = self._empty_ext()
        iri, is_new, _ = resolve_suffix("T1w", ext, neuropoly_vocab_path=p)
        assert iri == "nb:CustomT1"
        assert is_new is True

    def test_source_marked_as_neuropoly_vocab(self, tmp_path):
        p = _make_vocab_file(tmp_path, [
            {"name": "Bright-field microscopy",
                "id": "BrightFieldMicroscopy", "abbreviation": "BF"},
        ])
        ext = self._empty_ext()
        resolve_suffix("BF", ext, neuropoly_vocab_path=p)
        assert ext["extensions"]["BF"]["source"] == "neuropoly_vocab"

    def test_vocab_file_term_stored_in_extensions(self, tmp_path):
        p = _make_vocab_file(tmp_path, [
            {"name": "TEM", "id": "TransmissionElectronMicroscopy",
                "abbreviation": "TEM"},
        ])
        ext = self._empty_ext()
        resolve_suffix("TEM", ext, neuropoly_vocab_path=p)
        assert "TEM" in ext["extensions"]
        assert ext["extensions"]["TEM"]["iri"] == "nb:TransmissionElectronMicroscopy"

    def test_missing_vocab_file_falls_through_to_nidm_aliases(self, tmp_path):
        """When vocab file doesn't exist, nidm: aliases must still work."""
        ext = self._empty_ext()
        iri, _, _ = resolve_suffix(
            "UNIT1", ext, neuropoly_vocab_path=tmp_path / "missing.json")
        assert iri == "nidm:T1Weighted"

    def test_vocab_file_suffix_not_present_falls_through(self, tmp_path):
        p = _make_vocab_file(tmp_path, [
            {"name": "BF", "id": "BrightFieldMicroscopy", "abbreviation": "BF"},
        ])
        ext = self._empty_ext()
        # TEM is not in this minimal vocab file → falls through to _NB_FALLBACKS
        iri, _, _ = resolve_suffix("TEM", ext, neuropoly_vocab_path=p)
        assert iri == "nb:TransmissionElectronMicroscopy"

    def test_cache_takes_priority_over_vocab_file(self, tmp_path):
        p = _make_vocab_file(tmp_path, [
            {"name": "BF", "id": "BrightFieldMicroscopy", "abbreviation": "BF"},
        ])
        ext = {
            "version": "1",
            "extensions": {
                "BF": {"iri": "nb:CachedBF", "description": "cached"},
            },
        }
        iri, is_new, _ = resolve_suffix("BF", ext, neuropoly_vocab_path=p)
        assert iri == "nb:CachedBF"
        assert is_new is False

    def test_real_vocab_file_resolves_all_13_suffixes(self):
        """Every abbreviation in the real vocab file must resolve via step 2."""
        import json
        block = json.loads(_REAL_VOCAB.read_text(encoding="utf-8"))[0]
        prefix = block["namespace_prefix"]
        for term in block["terms"]:
            abbr = term["abbreviation"]
            expected_iri = f"{prefix}:{term['id']}"
            ext = {"version": "1", "extensions": {}}
            iri, is_new, _ = resolve_suffix(
                abbr, ext, neuropoly_vocab_path=_REAL_VOCAB)
            assert iri == expected_iri, f"Suffix '{abbr}': expected {expected_iri!r}, got {iri!r}"
            assert is_new is True
            assert ext["extensions"][abbr]["source"] == "neuropoly_vocab"


# ===========================================================================
# _promote_to_neuropoly_vocab
# ===========================================================================

class TestPromoteToNeuropolyVocab:

    def test_adds_new_term_to_existing_file(self, tmp_path):
        p = _make_vocab_file(tmp_path, [
            {"name": "BF", "id": "BrightFieldMicroscopy", "abbreviation": "BF"},
        ])
        warnings = []
        _promote_to_neuropoly_vocab(
            "NEWMOD", "nb:NewModality", "New modality.", p, warnings)
        blocks = json.loads(p.read_text(encoding="utf-8"))
        ids = {t["id"] for t in blocks[0]["terms"]}
        assert "NewModality" in ids
        assert warnings == []

    def test_does_not_duplicate_existing_term(self, tmp_path):
        p = _make_vocab_file(tmp_path, [
            {"name": "BF", "id": "BrightFieldMicroscopy", "abbreviation": "BF"},
        ])
        warnings = []
        _promote_to_neuropoly_vocab(
            "BF", "nb:BrightFieldMicroscopy", "BF.", p, warnings)
        blocks = json.loads(p.read_text(encoding="utf-8"))
        terms = blocks[0]["terms"]
        assert sum(1 for t in terms if t["id"] == "BrightFieldMicroscopy") == 1
        assert warnings == []

    def test_creates_new_file_when_absent(self, tmp_path):
        p = tmp_path / "new_vocab.json"
        assert not p.exists()
        warnings = []
        _promote_to_neuropoly_vocab(
            "NEWMOD", "nb:NewModality", "New modality.", p, warnings)
        assert p.exists()
        blocks = json.loads(p.read_text(encoding="utf-8"))
        assert blocks[0]["terms"][0]["id"] == "NewModality"

    def test_adds_correct_fields_to_new_term(self, tmp_path):
        p = _make_vocab_file(tmp_path, [])
        warnings = []
        _promote_to_neuropoly_vocab(
            "XMod", "nb:XModality", "X modality.", p, warnings)
        blocks = json.loads(p.read_text(encoding="utf-8"))
        term = blocks[0]["terms"][0]
        assert term["id"] == "XModality"
        assert term["abbreviation"] == "XMod"
        assert term["name"] == "X modality."

    def test_invalid_local_name_appends_pending_warning(self, tmp_path):
        p = _make_vocab_file(tmp_path, [])
        warnings = []
        # IRI with spaces in local name — invalid
        _promote_to_neuropoly_vocab("BAD", "nb:Bad Name", "Bad.", p, warnings)
        assert any("vocab_extension_pending" in w for w in warnings)

    def test_invalid_local_name_does_not_write_file(self, tmp_path):
        p = _make_vocab_file(tmp_path, [])
        mtime_before = p.stat().st_mtime
        import time
        time.sleep(0.01)
        warnings = []
        _promote_to_neuropoly_vocab("BAD", "nb:Bad Name", "Bad.", p, warnings)
        assert p.stat().st_mtime == mtime_before

    def test_no_tmp_file_lingers_after_success(self, tmp_path):
        p = _make_vocab_file(tmp_path, [])
        warnings = []
        _promote_to_neuropoly_vocab("XMod", "nb:XModality", "X.", p, warnings)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_creates_new_namespace_block_when_prefix_not_present(self, tmp_path):
        p = _make_vocab_file(tmp_path, [
            {"name": "Term A", "id": "TermA", "abbreviation": "TA"},
        ], prefix="nidm")
        warnings = []
        _promote_to_neuropoly_vocab("NB", "nb:NbTerm", "NB term.", p, warnings)
        blocks = json.loads(p.read_text(encoding="utf-8"))
        prefixes = {b["namespace_prefix"] for b in blocks}
        assert "nb" in prefixes
        assert "nidm" in prefixes

    def test_io_error_appends_pending_warning(self, tmp_path):
        # Make the vocab directory read-only to prevent .tmp file creation
        sub = tmp_path / "sub"
        sub.mkdir()
        p = _make_vocab_file(sub, [])
        sub.chmod(0o555)  # read + execute only on directory
        try:
            warnings = []
            _promote_to_neuropoly_vocab(
                "XMod", "nb:XModality", "X.", p, warnings)
            assert any("vocab_extension_pending" in w for w in warnings)
        finally:
            sub.chmod(0o755)  # restore

    def test_io_error_does_not_raise(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        p = _make_vocab_file(sub, [])
        sub.chmod(0o555)
        try:
            warnings = []
            # Must not raise
            _promote_to_neuropoly_vocab(
                "XMod", "nb:XModality", "X.", p, warnings)
        finally:
            sub.chmod(0o755)


# ===========================================================================
# build_extra_mapping — neuropoly_vocab_path integration
# ===========================================================================

class TestBuildExtraMappingWithVocabPath:

    def test_vocab_path_resolves_suffix_from_file(self, tmp_path):
        ext_cfg = tmp_path / "extensions.json"
        vocab = _make_vocab_file(tmp_path, [
            {"name": "Bright-field microscopy",
                "id": "BrightFieldMicroscopy", "abbreviation": "BF"},
        ])
        extra, _ = build_extra_mapping(
            ["BF"], ext_cfg, neuropoly_vocab_path=vocab)
        assert extra["BF"] == "nb:BrightFieldMicroscopy"

    def test_source_is_neuropoly_vocab_in_extensions_file(self, tmp_path):
        ext_cfg = tmp_path / "extensions.json"
        vocab = _make_vocab_file(tmp_path, [
            {"name": "TEM", "id": "TransmissionElectronMicroscopy",
                "abbreviation": "TEM"},
        ])
        build_extra_mapping(["TEM"], ext_cfg, neuropoly_vocab_path=vocab)
        data = load_extensions(ext_cfg)
        assert data["extensions"]["TEM"]["source"] == "neuropoly_vocab"

    def test_llm_resolved_nb_term_promoted_into_vocab_file(self, tmp_path):
        """When LLM returns an nb: IRI and vocab_path is given, the term is promoted."""
        from unittest.mock import MagicMock
        ext_cfg = tmp_path / "extensions.json"
        vocab = _make_vocab_file(tmp_path, [])
        mock_client = MagicMock()
        mock_client.chat.return_value = (
            '{"iri": "nb:SuperNewModality", "description": "A super new modality."}'
        )
        build_extra_mapping(
            ["SUPERNEW"], ext_cfg, ai_client=mock_client, neuropoly_vocab_path=vocab
        )
        blocks = json.loads(vocab.read_text(encoding="utf-8"))
        ids = {t["id"] for t in blocks[0]["terms"]}
        assert "SuperNewModality" in ids

    def test_generic_fallback_nb_term_promoted_into_vocab_file(self, tmp_path):
        ext_cfg = tmp_path / "extensions.json"
        vocab = _make_vocab_file(tmp_path, [])
        build_extra_mapping(["UNKMOD"], ext_cfg, neuropoly_vocab_path=vocab)
        blocks = json.loads(vocab.read_text(encoding="utf-8"))
        # Generic fallback produces nb:UNKMODImage
        ids = {t["id"] for t in blocks[0]["terms"]}
        assert "UNKMODImage" in ids

    def test_nidm_aliased_term_not_promoted_into_vocab_file(self, tmp_path):
        """nidm: terms must never be written into the nb: vocab file."""
        ext_cfg = tmp_path / "extensions.json"
        vocab = _make_vocab_file(tmp_path, [])
        build_extra_mapping(["UNIT1"], ext_cfg, neuropoly_vocab_path=vocab)
        blocks = json.loads(vocab.read_text(encoding="utf-8"))
        ids = {t["id"] for t in blocks[0].get("terms", [])}
        assert "T1Weighted" not in ids  # nidm term — must not appear here
        # File should have no terms
        assert len(ids) == 0

    def test_already_in_vocab_file_not_duplicated_on_second_call(self, tmp_path):
        ext_cfg = tmp_path / "extensions.json"
        vocab = _make_vocab_file(tmp_path, [
            {"name": "BF", "id": "BrightFieldMicroscopy", "abbreviation": "BF"},
        ])
        build_extra_mapping(["BF"], ext_cfg, neuropoly_vocab_path=vocab)
        build_extra_mapping(["BF"], ext_cfg, neuropoly_vocab_path=vocab)
        blocks = json.loads(vocab.read_text(encoding="utf-8"))
        bf_count = sum(1 for t in blocks[0]["terms"]
                       if t["id"] == "BrightFieldMicroscopy")
        assert bf_count == 1

    def test_vocab_extension_pending_in_warnings_on_io_error(self, tmp_path):
        """When vocab file is unwritable, a vocab_extension_pending warning propagates through build_extra_mapping."""
        from unittest.mock import MagicMock
        ext_cfg = tmp_path / "extensions.json"
        sub = tmp_path / "vocab_dir"
        sub.mkdir()
        vocab = _make_vocab_file(sub, [])
        mock_client = MagicMock()
        mock_client.chat.return_value = (
            '{"iri": "nb:UnknownMod", "description": "An unknown modality."}'
        )
        sub.chmod(0o555)  # block .tmp creation in the vocab dir
        try:
            _, warnings = build_extra_mapping(
                ["UNKMOD"], ext_cfg, ai_client=mock_client, neuropoly_vocab_path=vocab
            )
            assert any("vocab_extension_pending" in w for w in warnings)
        finally:
            sub.chmod(0o755)

    def test_no_vocab_path_no_promotion_no_error(self, tmp_path):
        """When neuropoly_vocab_path is None, promotion is silently skipped."""
        ext_cfg = tmp_path / "extensions.json"
        extra, warnings = build_extra_mapping(["UNKMOD"], ext_cfg)
        assert "UNKMOD" in extra
        assert not any("vocab_extension_pending" in w for w in warnings)

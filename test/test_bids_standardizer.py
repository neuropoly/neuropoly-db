"""
Tests for BIDS standardization pipeline.

Covers:
- Header renaming (exact, fuzzy, no-match)
- Missing column insertion
- participants.json generation (BIDS-valid, annotations, merge)
- BIDS sidecar validation
- Dry-run behavior
- End-to-end pipeline
"""

import json
import shutil
from pathlib import Path

import pytest

from npdb.annotation import AnnotationConfig
from npdb.annotation.standardize import (
    BIDS_VALID_SIDECAR_FIELDS,
    add_missing_standard_columns,
    expand_iri,
    generate_participants_json,
    rename_tsv_headers,
    validate_bids_sidecar,
)
from npdb.automation.mappings.resolvers import MappingResolver, ResolvedMapping
from npdb.managers.bids import BIDSStandardizer

FIXTURES_DIR = Path(__file__).parent / "fixtures" / \
    "datasets" / "unstandardized_bids"


# ── Helpers ────────────────────────────────────────────────────────


def _make_bids_dir(tmp_path: Path, tsv_content: str) -> Path:
    """Create a minimal BIDS dir with given TSV content."""
    bids = tmp_path / "bids_dataset"
    bids.mkdir()
    (bids / "participants.tsv").write_text(tsv_content, encoding="utf-8")
    (bids / "dataset_description.json").write_text(
        json.dumps({"Name": "test", "BIDSVersion": "1.9.0"}),
        encoding="utf-8",
    )
    return bids


def _read_tsv_headers(tsv_path: Path) -> list[str]:
    with open(tsv_path, "r", encoding="utf-8") as f:
        return f.readline().rstrip("\n").split("\t")


# ── rename_tsv_headers tests ──────────────────────────────────────


class TestRenameTsvHeaders:
    def test_exact_match_unchanged(self, tmp_path):
        """participant_id already canonical — not renamed."""
        bids = _make_bids_dir(
            tmp_path,
            "participant_id\tage\tsex\nsub-01\t25\tM\n",
        )
        tsv = bids / "participants.tsv"
        resolver = MappingResolver()
        resolved = resolver.resolve_columns(["participant_id", "age", "sex"])
        rename_map = rename_tsv_headers(tsv, resolved)
        # participant_id, age, sex are all exact keys — no renames
        assert rename_map == {}
        assert _read_tsv_headers(tsv) == ["participant_id", "age", "sex"]

    def test_fuzzy_alias_rename(self, tmp_path):
        """partid (alias of participant_id) should be renamed."""
        bids = _make_bids_dir(
            tmp_path,
            "partid\tage\nsub-01\t25\n",
        )
        tsv = bids / "participants.tsv"
        resolver = MappingResolver()
        resolved = resolver.resolve_columns(["partid", "age"])
        rename_map = rename_tsv_headers(tsv, resolved)
        # partid is a known alias → participant_id
        assert "partid" in rename_map
        assert rename_map["partid"] == "participant_id"
        assert _read_tsv_headers(tsv)[0] == "participant_id"

    def test_no_match_unchanged(self, tmp_path):
        """Unknown columns remain unchanged."""
        bids = _make_bids_dir(
            tmp_path,
            "participant_id\tmy_custom_col\nsub-01\tvalue\n",
        )
        tsv = bids / "participants.tsv"
        resolver = MappingResolver()
        resolved = resolver.resolve_columns(
            ["participant_id", "my_custom_col"])
        rename_map = rename_tsv_headers(tsv, resolved)
        assert "my_custom_col" not in rename_map
        assert "my_custom_col" in _read_tsv_headers(tsv)

    def test_dry_run_no_write(self, tmp_path):
        """Dry-run returns rename map but does not modify file."""
        bids = _make_bids_dir(
            tmp_path,
            "partid\tage\nsub-01\t25\n",
        )
        tsv = bids / "participants.tsv"
        original = tsv.read_text()
        resolver = MappingResolver()
        resolved = resolver.resolve_columns(["partid", "age"])
        rename_map = rename_tsv_headers(tsv, resolved, dry_run=True)
        assert "partid" in rename_map
        # File unchanged
        assert tsv.read_text() == original


# ── add_missing_standard_columns tests ────────────────────────────


class TestAddMissingColumns:
    def test_adds_missing_columns(self, tmp_path):
        """Missing standard columns are appended with n/a."""
        bids = _make_bids_dir(
            tmp_path,
            "participant_id\nsub-01\n",
        )
        tsv = bids / "participants.tsv"
        resolver = MappingResolver()
        added = add_missing_standard_columns(tsv, resolver.mappings)
        assert len(added) > 0
        headers = _read_tsv_headers(tsv)
        for col in added:
            assert col in headers

        # Data row should have n/a for added columns
        with open(tsv, "r", encoding="utf-8") as f:
            f.readline()  # skip header
            data_line = f.readline().rstrip("\n").split("\t")
        # Added columns come after participant_id → should be n/a
        assert "n/a" in data_line

    def test_none_missing(self, tmp_path):
        """No columns added when all standard columns present."""
        resolver = MappingResolver()
        all_keys = list(resolver.mappings.get("mappings", {}).keys())
        header = "\t".join(all_keys)
        data = "\t".join(["val"] * len(all_keys))
        bids = _make_bids_dir(tmp_path, f"{header}\n{data}\n")
        tsv = bids / "participants.tsv"
        added = add_missing_standard_columns(tsv, resolver.mappings)
        assert added == []


# ── generate_participants_json tests ──────────────────────────────


class TestGenerateParticipantsJson:
    def test_bids_valid_output(self, tmp_path):
        """Generated JSON contains only BIDS-valid fields."""
        bids = _make_bids_dir(
            tmp_path,
            "participant_id\tage\tsex\nsub-01\t25\tM\n",
        )
        tsv = bids / "participants.tsv"
        resolver = MappingResolver()
        resolved = resolver.resolve_columns(["participant_id", "age", "sex"])
        sidecar = generate_participants_json(
            tsv, resolved, resolver.mappings,
            keep_annotations=False,
        )
        for col_name, entry in sidecar.items():
            if isinstance(entry, dict):
                for field in entry:
                    assert field in BIDS_VALID_SIDECAR_FIELDS, (
                        f"Non-BIDS field '{field}' in column '{col_name}'"
                    )

    def test_strips_annotations_by_default(self, tmp_path):
        """Annotations block is absent when keep_annotations=False."""
        bids = _make_bids_dir(
            tmp_path,
            "participant_id\tsex\nsub-01\tM\n",
        )
        tsv = bids / "participants.tsv"
        resolver = MappingResolver()
        resolved = resolver.resolve_columns(["participant_id", "sex"])
        sidecar = generate_participants_json(
            tsv, resolved, resolver.mappings,
            keep_annotations=False,
        )
        for entry in sidecar.values():
            if isinstance(entry, dict):
                assert "Annotations" not in entry

    def test_keep_annotations(self, tmp_path):
        """Annotations block present when keep_annotations=True."""
        bids = _make_bids_dir(
            tmp_path,
            "participant_id\tsex\nsub-01\tM\n",
        )
        tsv = bids / "participants.tsv"
        resolver = MappingResolver()
        resolved = resolver.resolve_columns(["participant_id", "sex"])
        sidecar = generate_participants_json(
            tsv, resolved, resolver.mappings,
            keep_annotations=True,
        )
        # sex has mapping data → should have Annotations
        assert "Annotations" in sidecar.get("sex", {})

    def test_merge_existing_json(self, tmp_path):
        """Existing user content in participants.json is preserved."""
        bids = _make_bids_dir(
            tmp_path,
            "participant_id\tage\nsub-01\t25\n",
        )
        tsv = bids / "participants.tsv"
        existing = bids / "participants.json"
        existing.write_text(
            json.dumps({
                "age": {"Description": "User-written age description", "Units": "months"}
            }),
            encoding="utf-8",
        )
        resolver = MappingResolver()
        resolved = resolver.resolve_columns(["participant_id", "age"])
        sidecar = generate_participants_json(
            tsv, resolved, resolver.mappings,
            existing_json_path=existing,
        )
        # User-written Description should be preserved (not overwritten)
        assert sidecar["age"]["Description"] == "User-written age description"
        # User-written Units should be preserved
        assert sidecar["age"]["Units"] == "months"


# ── validate_bids_sidecar tests ──────────────────────────────────


class TestValidateBidsSidecar:
    def test_strips_invalid_fields(self):
        """Non-BIDS fields are stripped with warnings."""
        sidecar = {
            "age": {
                "Description": "Age",
                "Annotations": {"IsAbout": {"TermURL": "nb:Age"}},
                "CustomField": "should be removed",
            }
        }
        cleaned, warnings = validate_bids_sidecar(sidecar)
        assert "Annotations" not in cleaned["age"]
        assert "CustomField" not in cleaned["age"]
        assert "Description" in cleaned["age"]
        assert len(warnings) == 2

    def test_passes_valid_fields(self):
        """BIDS-valid fields are kept unchanged."""
        sidecar = {
            "sex": {
                "LongName": "Sex",
                "Description": "Biological sex",
                "Levels": {"M": "Male", "F": "Female"},
            }
        }
        cleaned, warnings = validate_bids_sidecar(sidecar)
        assert cleaned == sidecar
        assert warnings == []


# ── Levels and TermURL format tests ──────────────────────────────


class TestBidsFormats:
    def test_levels_bids_format(self, tmp_path):
        """Levels use BIDS structured format with Description/TermURL."""
        bids = _make_bids_dir(
            tmp_path,
            "sex\nM\n",
        )
        tsv = bids / "participants.tsv"
        resolver = MappingResolver()
        resolved = resolver.resolve_columns(["sex"])
        sidecar = generate_participants_json(
            tsv, resolved, resolver.mappings,
        )
        levels = sidecar.get("sex", {}).get("Levels", {})
        assert "M" in levels
        # Should be a dict with Description and/or TermURL
        m_entry = levels["M"]
        assert isinstance(m_entry, dict)
        assert "Description" in m_entry or "TermURL" in m_entry

    def test_termurl_expanded(self):
        """Abbreviated IRIs are expanded to full URLs."""
        url = expand_iri("snomed:248153007")
        assert url.startswith("http")
        assert "248153007" in url
        assert "snomed:" not in url

    def test_termurl_passthrough(self):
        """Full URLs are not modified."""
        full = "http://example.org/vocab#term"
        assert expand_iri(full) == full


# ── Dry-run end-to-end ───────────────────────────────────────────


class TestDryRun:
    def test_dry_run_no_file_writes(self, tmp_path):
        """Dry-run does not create or modify any files."""
        bids = _make_bids_dir(
            tmp_path,
            "partid\tsubject_age\tgender\nsub-01\t25\tM\n",
        )
        original_tsv = (bids / "participants.tsv").read_text()
        assert not (bids / "participants.json").exists()

        config = AnnotationConfig(mode="auto", dry_run=True)
        standardizer = BIDSStandardizer(config)

        import asyncio
        result = asyncio.run(standardizer.execute(input_path=bids))
        assert result is True

        # TSV unchanged
        assert (bids / "participants.tsv").read_text() == original_tsv
        # No JSON created
        assert not (bids / "participants.json").exists()
        # No provenance created
        assert not (bids / "participants_provenance.json").exists()


# ── End-to-end pipeline test ─────────────────────────────────────


class TestEndToEnd:
    def test_standardize_full_pipeline(self, tmp_path):
        """Full pipeline: rename + add columns + generate JSON."""
        # Copy fixture to tmp
        bids = tmp_path / "bids_dataset"
        shutil.copytree(FIXTURES_DIR, bids)

        config = AnnotationConfig(mode="auto")
        standardizer = BIDSStandardizer(config)

        import asyncio
        result = asyncio.run(standardizer.execute(input_path=bids))
        assert result is True

        # participants.json should exist
        json_path = bids / "participants.json"
        assert json_path.exists()
        sidecar = json.loads(json_path.read_text())

        # Should have entries for columns
        assert len(sidecar) > 0

        # All entries should be BIDS-valid
        for col_name, entry in sidecar.items():
            if isinstance(entry, dict):
                for field in entry:
                    assert field in BIDS_VALID_SIDECAR_FIELDS, (
                        f"Non-BIDS field '{field}' in column '{col_name}'"
                    )

        # Provenance should exist
        prov_path = bids / "participants_provenance.json"
        assert prov_path.exists()

    def test_idempotent(self, tmp_path):
        """Running twice produces same result."""
        bids = tmp_path / "bids_dataset"
        shutil.copytree(FIXTURES_DIR, bids)

        config = AnnotationConfig(mode="auto")

        import asyncio

        # First run
        s1 = BIDSStandardizer(config)
        asyncio.run(s1.execute(input_path=bids))
        tsv_after_first = (bids / "participants.tsv").read_text()
        json_after_first = (bids / "participants.json").read_text()

        # Second run
        s2 = BIDSStandardizer(config)
        asyncio.run(s2.execute(input_path=bids))
        tsv_after_second = (bids / "participants.tsv").read_text()
        json_after_second = (bids / "participants.json").read_text()

        assert tsv_after_first == tsv_after_second
        assert json_after_first == json_after_second

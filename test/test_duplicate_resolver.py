"""
Test duplicate field resolution using benchmark data.

Demonstrates how multiple columns mapping to the same Neurobagel variable
are intelligently resolved:
- Identifiers: primary kept as-is, ONE alternate renamed to alt_*
- Other variables: highest-confidence kept, others removed
"""

import json
import tempfile
from pathlib import Path

from npdb.annotation.duplicates import resolve_phenotype_duplicates


def test_duplicate_resolution_with_benchmark_data():
    """
    Test duplicate resolution using flat-format test data.
    Tests resolution of 'participant_id' and 'source_id' both mapping to nb:ParticipantID.

    Expected behavior:
    - participant_id: primary, stays as-is
    - source_id: alternate, renamed to alt_participant_id in TSV, removed from JSON
    """
    # Create temporary directory for test
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create test TSV with duplicate columns
        test_tsv_path = tmpdir / "phenotypes.tsv"
        tsv_content = """participant_id	source_id	age	sex
sub-001	source_001	25	M
sub-002	source_002	30	F
sub-003	source_003	28	M"""
        with open(test_tsv_path, 'w') as f:
            f.write(tsv_content)

        # Create test annotations in FLAT FORMAT (not Bagel format)
        # This represents what the resolver expects
        test_annotations_path = tmpdir / "phenotypes_annotations.json"
        flat_annotations = {
            "participant_id": {
                "variable": "nb:ParticipantID",
                "confidence": 1.0,
                "source": "static",
                "rationale": "Primary identifier"
            },
            "source_id": {
                "variable": "nb:ParticipantID",
                "confidence": 0.95,
                "source": "fuzzy",
                "rationale": "Alternate identifier"
            },
            "age": {
                "variable": "nb:Age",
                "confidence": 0.9,
                "source": "static",
                "rationale": "Age variable"
            },
            "sex": {
                "variable": "nb:Sex",
                "confidence": 0.95,
                "source": "static",
                "rationale": "Sex variable"
            }
        }
        with open(test_annotations_path, 'w') as f:
            json.dump(flat_annotations, f, indent=2)

        print("\n=== Test: Duplicate Resolution with Flat-Format Data ===\n")
        print(f"Input TSV: {test_tsv_path}")
        print(f"Input annotations: {test_annotations_path}")

        # Load original state
        with open(test_annotations_path, 'r') as f:
            original_annotations = json.load(f)

        with open(test_tsv_path, 'r') as f:
            original_header = f.readline().strip().split('\t')

        print(f"\n→ Original state:")
        print(f"  TSV columns: {original_header}")
        print(f"  Annotations: {list(original_annotations.keys())}")
        for col, mapping in original_annotations.items():
            print(
                f"    {col} → {mapping['variable']} (confidence {mapping['confidence']:.2f})")

        # Run duplicate resolver
        resolve_phenotype_duplicates(
            test_tsv_path,
            test_annotations_path,
            verbose=True
        )

        # Load resolved state
        with open(test_annotations_path, 'r') as f:
            resolved_annotations = json.load(f)

        with open(test_tsv_path, 'r') as f:
            resolved_header = f.readline().strip().split('\t')

        print(f"\n→ Resolved state:")
        print(f"  TSV columns: {resolved_header}")
        print(f"  Annotations: {list(resolved_annotations.keys())}")
        for col, mapping in resolved_annotations.items():
            print(
                f"    {col} → {mapping['variable']} (confidence {mapping['confidence']:.2f})")

        # Verify results
        print(f"\n→ Verification:")

        # Primary participant_id should be kept as-is in both
        assert 'participant_id' in resolved_header and 'participant_id' in resolved_annotations, \
            "Primary participant_id not preserved"
        print("  ✓ Primary participant_id kept in both TSV and JSON")

        # source_id should be renamed to alt_participant_id in TSV
        assert 'alt_participant_id' in resolved_header and 'source_id' not in resolved_header, \
            f"Alternate not renamed correctly. TSV columns: {resolved_header}"
        print("  ✓ Alternate renamed to alt_participant_id in TSV")

        # source_id should NOT be in JSON (unannotated)
        assert 'source_id' not in resolved_annotations, \
            "Alternate still in JSON annotations"
        print("  ✓ Alternate source_id removed from JSON annotations")

        # Non-duplicate columns should still exist
        assert all(col in resolved_header for col in ['age', 'sex']), \
            "Non-duplicate columns not preserved"
        print("  ✓ Non-duplicate columns preserved")

        print("\n✓ Test PASSED: Duplicate resolution works correctly!")

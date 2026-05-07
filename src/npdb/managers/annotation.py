"""
Annotator classes for Neurobagel and BIDS standardization.
"""

import json
import shutil
from pathlib import Path

from npdb.annotation import AnnotationConfig
from npdb.annotation.annotator import Annotator
from npdb.annotation.duplicates import resolve_phenotype_duplicates
from npdb.annotation.standardize import (
    add_missing_standard_columns,
    apply_header_map,
    generate_participants_json,
    header_map_variables,
    load_header_map,
    rename_tsv_headers,
    validate_bids_sidecar,
)
from npdb.annotation.strategies import AnnotationStrategyFactory, AnnotatorContext
from npdb.annotation.utils import parse_tsv_columns
from npdb.external.neurobagel.schema import convert_to_bagel_schema
from npdb.report.provenance import save_provenance


class NeurobagelAnnotator(Annotator):
    """
    Orchestrates phenotype annotation automation across 4 modes.

    Integrates:
    - Browser session management (Playwright)
    - Mapping resolution (static dict + fuzzy matching)
    - Provenance tracking (audit trail)
    - Mode-specific orchestration (manual/assist/auto/full-auto)
    """

    def __init__(self, config: AnnotationConfig):
        super().__init__(config)

    async def _save_outputs(
        self,
        participants_tsv_path: Path,
        output_dir: Path,
        annotations_dict: dict,
    ) -> None:
        """
        Save phenotypes.tsv and phenotypes_annotations.json to output directory.

        Processing pipeline:
        1. Save flat-format annotations and TSV
        2. Apply duplicate resolver (modifies TSV + JSON in-place)
        3. Load resolved flat format and convert to Bagel schema
        4. Save Bagel-compliant format

        Args:
            participants_tsv_path: Path to input participants.tsv
            output_dir: Output directory where files should be saved
            annotations_dict: Mapping annotations as dictionary (flat format)
        """
        phenotypes_tsv_path = output_dir / "phenotypes.tsv"
        shutil.copy2(participants_tsv_path, phenotypes_tsv_path)
        print(f"✓ Saved phenotypes.tsv: {phenotypes_tsv_path}")

        # Step 1: Save flat-format annotations to JSON
        phenotypes_annotations_path = output_dir / "phenotypes_annotations.json"
        with open(phenotypes_annotations_path, "w") as f:
            json.dump(annotations_dict, f, indent=2)
        print(f"✓ Saved flat-format annotations: {phenotypes_annotations_path}")

        # Step 2: Apply duplicate resolver (modifies both files in-place)
        # This resolves duplicates in both TSV columns and JSON annotations
        print(f"→ Resolving duplicate field mappings...")
        resolve_phenotype_duplicates(
            phenotypes_tsv_path, phenotypes_annotations_path, verbose=True
        )

        # Step 3: Load resolved flat-format annotations
        with open(phenotypes_annotations_path, "r") as f:
            resolved_annotations_flat = json.load(f)

        # Step 4: Convert resolved annotations to Bagel schema
        # Note: self.resolver.mappings is the full phenotype_mappings structure with @context and mappings keys
        phenotype_mappings_dict = self.resolver.mappings
        resolved_annotations_bagel = convert_to_bagel_schema(
            resolved_annotations_flat, phenotype_mappings_dict
        )

        # Step 5: Save Bagel-compliant format (overwrites flat format)
        with open(phenotypes_annotations_path, "w") as f:
            json.dump(resolved_annotations_bagel, f, indent=2)
        print(f"✓ Saved Bagel-compliant annotations: {phenotypes_annotations_path}")

    async def execute(
        self,
        participants_tsv_path: Path,
        output_dir: Path,
    ) -> bool:
        """
        Execute annotation automation according to configured mode.

        Args:
            participants_tsv_path: Path to participants.tsv file
            output_dir: Output directory for phenotypes files

        Returns:
            True on success, False on failure.
        """
        if not participants_tsv_path.exists():
            raise FileNotFoundError(
                f"Participants TSV not found: {participants_tsv_path}"
            )

        output_dir.mkdir(parents=True, exist_ok=True)

        # Apply user-supplied header translation map before any annotation mode
        if self.config.header_map:
            hmap = load_header_map(self.config.header_map)
            pre_renames = apply_header_map(participants_tsv_path, hmap)
            if pre_renames:
                print(f"✓ Header map applied: renamed {len(pre_renames)} columns")
                for old, new in pre_renames.items():
                    print(f"  {old} → {new}")

        ctx = AnnotatorContext(
            config=self.config,
            resolver=self.resolver,
            provenance=self.provenance,
            save_outputs=self._save_outputs,
        )
        strategy = AnnotationStrategyFactory.create(self.config)
        return await strategy.run(participants_tsv_path, output_dir, ctx)


class BIDSStandardizer(Annotator):
    """
    Standardizes BIDS dataset participants.tsv and participants.json.

    Workflow:
    1. Parse TSV columns
    2. Resolve columns via MappingResolver
    3. Rename headers to canonical names
    4. Add missing standard columns
    5. Generate BIDS-compliant participants.json
    6. Save provenance report
    """

    async def execute(self, input_path: Path, output_dir: Path | None = None) -> bool:
        """
        Execute BIDS standardization on a dataset.

        Args:
            input_path: Path to BIDS dataset root (must contain participants.tsv).
            output_dir: Unused for BIDS (edits in-place). Kept for interface compat.

        Returns:
            True if successful, False on failure.
        """
        bids_root = input_path
        tsv_path = bids_root / "participants.tsv"

        if not tsv_path.exists():
            raise FileNotFoundError(f"participants.tsv not found in {bids_root}")

        self.provenance.dataset_name = bids_root.name
        dry_run = self.config.dry_run
        keep_annotations = self.config.keep_annotations

        try:
            # Step 0 (optional): Apply user-supplied header translation map
            pre_renames = {}
            header_map_keys = set()
            hmap_variables: set[str] = set()
            hmap = None
            if self.config.header_map:
                hmap = load_header_map(self.config.header_map)
                header_map_keys = set(hmap.keys())
                hmap_variables = header_map_variables(hmap)
                pre_renames = apply_header_map(tsv_path, hmap, dry_run=dry_run)
                if pre_renames:
                    print(f"✓ Header map applied: renamed {len(pre_renames)} columns")
                    for old, new in pre_renames.items():
                        print(f"  {old} → {new}")

            # Step 1: Parse columns
            column_names = parse_tsv_columns(tsv_path)
            print(f"✓ Parsed {len(column_names)} columns from participants.tsv")

            # Step 2: Resolve and track provenance
            annotations_dict, resolved_mappings = self.resolve_and_track(column_names)
            print(f"✓ Resolved {len(annotations_dict)}/{len(column_names)} columns")

            # Step 3: Rename headers (protect header-map keys from override)
            rename_map = rename_tsv_headers(
                tsv_path,
                resolved_mappings,
                dry_run=dry_run,
                protected_columns=header_map_keys,
            )
            if rename_map:
                print(f"✓ Renamed {len(rename_map)} column headers")
                for old, new in rename_map.items():
                    print(f"  {old} → {new}")

            # Step 4: Add missing standard columns
            added = None
            if not self.config.no_new_columns:
                added = add_missing_standard_columns(
                    tsv_path,
                    self.resolver.mappings,
                    dry_run=dry_run,
                    extra_covered_variables=hmap_variables or None,
                )
                if added:
                    print(f"✓ Added {len(added)} missing standard columns: {added}")

            # In dry-run mode, the TSV was not modified by rename/add steps.
            # Compute the effective column list so generate_participants_json
            # produces output matching what would actually be written.
            effective_columns = None
            if dry_run:
                # Apply header-map renames, then resolver renames, then added cols
                effective_columns = [
                    rename_map.get(pre_renames.get(c, c), pre_renames.get(c, c))
                    for c in column_names
                ] + (added or [])

            # Step 5: Generate participants.json
            existing_json = bids_root / "participants.json"
            sidecar = generate_participants_json(
                tsv_path,
                resolved_mappings,
                self.resolver.mappings,
                existing_json_path=existing_json if existing_json.exists() else None,
                keep_annotations=keep_annotations,
                dry_run=dry_run,
                column_names=effective_columns,
                header_map=hmap,
            )

            if not dry_run:
                # Validate and report warnings
                if not keep_annotations:
                    _, warnings = validate_bids_sidecar(sidecar)
                    for w in warnings:
                        print(f"  ⚠ {w}")
                        self.provenance.warnings.append(w)

                # Write participants.json
                json_path = bids_root / "participants.json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(sidecar, f, indent=2)
                    f.write("\n")
                print(f"✓ Saved participants.json: {json_path}")

                # Step 6: Save provenance
                provenance_path = bids_root / "participants_provenance.json"
                save_provenance(self.provenance, provenance_path)
                print(f"✓ Saved provenance: {provenance_path}")
            else:
                # Dry-run: still report validation warnings
                if not keep_annotations:
                    _, warnings = validate_bids_sidecar(sidecar)
                    for w in warnings:
                        print(f"  ⚠ {w}")

            return True

        except Exception as e:
            print(f"✗ BIDS standardization error: {e}")
            self.provenance.warnings.append(f"Standardization failed: {e}")
            return False

    async def _save_outputs(
        self,
        input_path: Path,
        output_dir: Path,
        annotations_dict: dict,
    ) -> None:
        """Not used directly — execute() handles output internally."""
        pass

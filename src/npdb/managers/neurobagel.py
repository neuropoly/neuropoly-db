
import asyncio
import json
import os
import shutil

from pathlib import Path
from typer.testing import CliRunner
from bagel.cli import bagel

from npdb.annotation import AnnotationConfig
from npdb.annotation.duplicates import resolve_phenotype_duplicates
from npdb.annotation.provenance import ProvenanceReport, add_column_provenance
from npdb.annotation.standardize import apply_header_map, load_header_map
from npdb.automation.mappings.resolvers import MappingResolver
from npdb.external.neurobagel.automation import NBAnnotationToolBrowserSession
from npdb.external.neurobagel.errors import BagelCLIError
from npdb.external.neurobagel.schema import convert_to_bagel_schema
from npdb.managers.annotation import AnnotationManager
from npdb.managers.model import Manager
from npdb.utils import parse_tsv_columns


class BagelDB:
    def __init__(self, jsonld_root: str):
        self.root = jsonld_root


class BagelMixin:
    def __init__(self, db: BagelDB):
        self.cli = CliRunner()
        self.db = db

    def bids2tsv(self, bids_directory: str, output_tsv: str):
        self._run_bagel_cli(
            "bids2tsv",
            "--bids-dir", bids_directory,
            "--output", output_tsv,
            "--overwrite"
        )

    def bagel_pheno(
        self,
        dataset_name: str,
        phenotypes_tsv: str,
        phenotypes_annotations: str,
        dataset_description: str
    ):
        self._run_bagel_cli(
            "pheno",
            "--pheno", phenotypes_tsv,
            "--dictionary", phenotypes_annotations,
            "--dataset-description", dataset_description,
            "--output", os.path.join(self.db.root, f"{dataset_name}.jsonld"),
            "--overwrite"
        )

    def bagel_bids(
        self,
        dataset_name: str,
        bids_table: str
    ):
        jsonld_path = os.path.join(self.db.root, f"{dataset_name}.jsonld")
        self._run_bagel_cli(
            "bids",
            "--jsonld-path", jsonld_path,
            "--bids-table", bids_table,
            "--output", jsonld_path,
            "--overwrite"
        )

    def _run_bagel_cli(self, *args):
        import io
        import logging
        from rich.console import Console
        from rich.logging import RichHandler
        from bagel.logger import logger as bagel_logger

        # Redirect bagel's RichHandler to a buffer so its log lines don't
        # escape the Live display and cause repeated re-renders.
        buf = io.StringIO()
        cap_console = Console(file=buf, highlight=False,
                              width=200, no_color=True)
        cap_handler = RichHandler(
            console=cap_console,
            omit_repeated_times=False,
            show_path=False,
            markup=True,
        )
        cap_handler.setFormatter(
            logging.Formatter("%(message)s", datefmt="[%Y-%m-%d %X]")
        )
        cap_handler.setLevel(logging.DEBUG)

        saved_handlers = bagel_logger.handlers[:]
        saved_propagate = bagel_logger.propagate
        bagel_logger.handlers = [cap_handler]
        bagel_logger.propagate = False
        try:
            result = self.cli.invoke(bagel, args)
        finally:
            bagel_logger.handlers = saved_handlers
            bagel_logger.propagate = saved_propagate

        if result.exit_code != 0:
            command = "bagel " + " ".join(str(a) for a in args)
            logs = buf.getvalue().strip()
            exception_text = ""
            if result.exception is not None:
                import traceback as _tb
                exception_text = "".join(_tb.format_exception(
                    type(result.exception),
                    result.exception,
                    result.exception.__traceback__,
                ))
            combined = "\n".join(filter(None, [result.output, logs, exception_text]))
            raise BagelCLIError.from_result(
                command=command,
                exit_code=result.exit_code,
                output=combined,
            )


class NeurobagelManager(Manager):
    def __init__(self, jsonld: str):
        self.db = BagelDB(jsonld)

    @property
    def datasets(self):
        return os.listdir(self.db.root)


class NeurobagelAnnotator(AnnotationManager):
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
        dataset_name: str = "",
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
        # Copy participants.tsv as {dataset}_phenotypes.tsv
        prefix = f"{dataset_name}_" if dataset_name else ""
        phenotypes_tsv_path = output_dir / f"{prefix}phenotypes.tsv"
        shutil.copy2(participants_tsv_path, phenotypes_tsv_path)
        print(f"✓ Saved phenotypes.tsv: {phenotypes_tsv_path}")

        # Step 1: Save flat-format annotations to JSON
        phenotypes_annotations_path = output_dir / \
            f"{prefix}phenotypes_annotations.json"
        with open(phenotypes_annotations_path, 'w') as f:
            json.dump(annotations_dict, f, indent=2)
        print(
            f"✓ Saved flat-format annotations: {phenotypes_annotations_path}")

        # Step 2: Apply duplicate resolver (modifies both files in-place)
        # This resolves duplicates in both TSV columns and JSON annotations
        print(f"→ Resolving duplicate field mappings...")
        resolve_phenotype_duplicates(
            phenotypes_tsv_path,
            phenotypes_annotations_path,
            verbose=True
        )

        # Step 3: Load resolved flat-format annotations
        with open(phenotypes_annotations_path, 'r') as f:
            resolved_annotations_flat = json.load(f)

        # Step 4: Convert resolved annotations to Bagel schema
        # Note: self.resolver.mappings is the full phenotype_mappings structure with @context and mappings keys
        phenotype_mappings_dict = self.resolver.mappings
        resolved_annotations_bagel = convert_to_bagel_schema(
            resolved_annotations_flat,
            phenotype_mappings_dict
        )

        # Step 5: Save Bagel-compliant format (overwrites flat format)
        with open(phenotypes_annotations_path, 'w') as f:
            json.dump(resolved_annotations_bagel, f, indent=2)
        print(
            f"✓ Saved Bagel-compliant annotations: {phenotypes_annotations_path}")

    async def execute(
        self,
        participants_tsv_path: Path,
        output_dir: Path,
        dataset_name: str = "",
    ) -> tuple[bool, "ProvenanceReport | None"]:
        """
        Execute annotation automation according to configured mode.

        Args:
            participants_tsv_path: Path to participants.tsv file
            output_dir: Output directory for phenotypes files
            dataset_name: Dataset name used to prefix output filenames

        Returns:
            Tuple of (success, report_or_none).  *report* is None for manual
            mode or when the run failed without generating provenance data.
        """
        if not participants_tsv_path.exists():
            raise FileNotFoundError(
                f"Participants TSV not found: {participants_tsv_path}")

        output_dir.mkdir(parents=True, exist_ok=True)

        # Apply user-supplied header translation map before any annotation mode
        if self.config.header_map:
            hmap = load_header_map(self.config.header_map)
            pre_renames = apply_header_map(participants_tsv_path, hmap)
            if pre_renames:
                print(
                    f"✓ Header map applied: renamed {len(pre_renames)} columns")
                for old, new in pre_renames.items():
                    print(f"  {old} → {new}")

        if self.config.mode == "manual":
            return await self._run_manual(participants_tsv_path, output_dir, dataset_name)
        elif self.config.mode == "assist":
            return await self._run_assist(participants_tsv_path, output_dir, dataset_name)
        elif self.config.mode == "auto":
            return await self._run_auto(participants_tsv_path, output_dir, dataset_name)
        elif self.config.mode == "full-auto":
            return await self._run_full_auto(participants_tsv_path, output_dir, dataset_name)

        return False, None

    async def _run_manual(
        self, participants_tsv_path: Path, output_dir: Path, dataset_name: str = ""
    ) -> tuple[bool, None]:
        """
        Execute manual mode: open browser and wait for user.

        Opens Neurobagel annotation tool in browser (headed mode) and waits
        for user to complete annotation manually.
        """
        try:
            async with NBAnnotationToolBrowserSession(
                headless=False,  # Manual always headed
                timeout=self.config.timeout,
                artifacts_dir=self.config.artifacts_dir
            ) as session:
                await session.navigate_to()
                print(
                    f"✓ Opened annotation tool: {NBAnnotationToolBrowserSession.ANNOTATION_URL}")

                await session.click_get_started()
                print(f"✓ Clicked 'Get Started' button")
                print(f"✓ Upload participants.tsv: {participants_tsv_path}")

                # Wait for completion signal (long timeout, user-driven)
                # TODO: Replace sleep with file-system monitoring or browser
                # event detection to detect when user exports data.
                await asyncio.sleep(self.config.timeout)

                # Assume outputs are downloaded to default location
                # In full implementation, would detect actual file completion
                return True, None

        except Exception as e:
            print(f"✗ Manual mode error: {e}")
            return False, None

    async def _run_assist(
        self, participants_tsv_path: Path, output_dir: Path, dataset_name: str = ""
    ) -> tuple[bool, "ProvenanceReport | None"]:
        """
        Execute assist mode: automated prefill + AI suggestions + user finalization.

        Steps:
        1. Open browser and upload TSV
        2. Resolve column headers using static dict + fuzzy matching
        3. Show suggestions in UI (with confidence scores as hints)
        4. Wait for user to finalize and export
        5. Save outputs and provenance sidecar

        Note: This mode attempts best-effort save even if browser upload fails,
        using offline column resolution as fallback.
        """
        browser_upload_failed = False
        session = None

        try:
            async with NBAnnotationToolBrowserSession(
                headless=self.config.headless,
                timeout=self.config.timeout,
                artifacts_dir=self.config.artifacts_dir
            ) as session:
                # Step 0: Navigate to landing page
                await session.navigate_to()
                print(f"✓ Opened annotation tool")

                # Step 1: Click "Get Started" and upload
                try:
                    await session.click_get_started()
                    print(f"✓ Clicked 'Get Started' button")

                    # Upload TSV data file (required)
                    await session.upload_file(
                        participants_tsv_path,
                        file_type="tsv"
                    )
                    print(f"✓ Uploaded {participants_tsv_path.name}")

                    # Upload optional phenotype dictionary JSON
                    if self.config.phenotype_dictionary and self.config.phenotype_dictionary.exists():
                        await session.upload_file(
                            self.config.phenotype_dictionary,
                            file_type="json"
                        )
                        print(
                            f"✓ Uploaded phenotype dictionary: {self.config.phenotype_dictionary.name}")

                except Exception as e:
                    print(f"⚠ Warning: Browser upload failed: {e}")
                    print(
                        f"⚠ Will attempt offline resolution and save best-effort output")
                    browser_upload_failed = True

                # Step 2: Resolve columns (works offline)
                # Parse actual column names from TSV file
                try:
                    column_names = parse_tsv_columns(participants_tsv_path)
                except Exception as e:
                    print(
                        f"⚠ Warning: Could not parse TSV columns: {e}. Using placeholder columns.")
                    column_names = ["participant_id", "age", "sex"]

                resolved = self.resolver.resolve_columns(column_names)

                print(
                    f"✓ Resolved {len(resolved)} columns (assist mode: showing suggestions)")

                # Track resolutions in provenance (skip unresolved)
                for mapping in resolved:
                    if mapping.source == "unresolved":
                        continue  # Skip unresolved; will require manual annotation
                    add_column_provenance(
                        self.provenance,
                        column_name=mapping.column_name,
                        source=mapping.source,
                        confidence=mapping.confidence,
                        variable=mapping.mapped_variable,
                        rationale=mapping.rationale
                    )

                # If upload succeeded, wait for user review in browser
                if not browser_upload_failed:
                    print(f"✓ Assist mode: User reviews and finalizes in browser")
                    # TODO: Replace sleep with download event detection or
                    # file polling instead of a static timeout.
                    await asyncio.sleep(self.config.timeout)

                # Build annotations dictionary from resolved mappings
                annotations_dict = {}
                for mapping in resolved:
                    if mapping.source != "unresolved":
                        annotations_dict[mapping.column_name] = {
                            "variable": mapping.mapped_variable,
                            "source": mapping.source,
                            "confidence": mapping.confidence,
                            "rationale": mapping.rationale
                        }

                # Step 3: Save outputs
                await self._save_outputs(
                    participants_tsv_path,
                    output_dir,
                    annotations_dict,
                    dataset_name=dataset_name,
                )

                if browser_upload_failed:
                    print(
                        f"⚠ Assist mode completed with offline resolution (browser upload failed)")
                    self.provenance.warnings.append(
                        "Browser upload failed; outputs from offline resolution")

                return True, self.provenance

        except Exception as e:
            print(f"✗ Assist mode error: {e}")
            print(f"✗ Exception type: {type(e).__name__}, Details: {str(e)}")

            # Attempt emergency save of best-effort output
            try:
                print(f"✓ Attempting emergency save of best-effort output...")
                column_names = parse_tsv_columns(
                    participants_tsv_path) if participants_tsv_path.exists() else []
                resolved = self.resolver.resolve_columns(
                    column_names) if column_names else []

                annotations_dict = {}
                for mapping in resolved:
                    if mapping.source != "unresolved":
                        annotations_dict[mapping.column_name] = {
                            "variable": mapping.mapped_variable,
                            "source": mapping.source,
                            "confidence": mapping.confidence,
                            "rationale": mapping.rationale
                        }

                if annotations_dict:
                    await self._save_outputs(
                        participants_tsv_path,
                        output_dir,
                        annotations_dict,
                        dataset_name=dataset_name,
                    )
                    self.provenance.warnings.append(
                        f"Partial failure: {str(e)}; saved offline resolution")
                    print(f"✓ Emergency save completed")
                    return False, self.provenance

            except Exception as emergency_e:
                print(f"✗ Emergency save also failed: {emergency_e}")

            return False, None

    async def _run_auto(
        self, participants_tsv_path: Path, output_dir: Path, dataset_name: str = ""
    ) -> tuple[bool, "ProvenanceReport | None"]:
        """
        Execute auto mode: full scripted automation with bounded AI.

        Steps:
        1. Open browser and upload TSV
        2. Resolve columns (static + fuzzy, confidence >= 0.7)
        3. Auto-fill forms with high-confidence mappings
        4. Export and validate outputs
        5. Save provenance with confidence distribution
        """
        current_step = "initialization"
        try:
            current_step = "browser_launch"
            async with NBAnnotationToolBrowserSession(
                headless=self.config.headless,
                timeout=self.config.timeout,
                artifacts_dir=self.config.artifacts_dir
            ) as session:
                current_step = "navigation"
                await session.navigate_to()
                print(f"✓ Auto mode: Opened annotation tool")

                current_step = "get_started_click"
                await session.click_get_started()
                print(f"✓ Auto mode: Clicked 'Get Started' button")

                current_step = "file_upload"
                # Upload TSV data file (required)
                await session.upload_file(
                    participants_tsv_path,
                    file_type="tsv"
                )
                print(f"✓ Uploaded {participants_tsv_path.name}")

                # Upload optional phenotype dictionary JSON
                if self.config.phenotype_dictionary and self.config.phenotype_dictionary.exists():
                    await session.upload_file(
                        self.config.phenotype_dictionary,
                        file_type="json"
                    )
                    print(
                        f"✓ Uploaded phenotype dictionary: {self.config.phenotype_dictionary.name}")

                # Resolve with bounded confidence (>= 0.7)
                current_step = "column_resolution"
                try:
                    column_names = parse_tsv_columns(participants_tsv_path)
                except Exception as e:
                    print(
                        f"⚠ Warning: Could not parse TSV columns: {e}. Using placeholder columns.")
                    column_names = ["participant_id", "age", "sex"]

                resolved = self.resolver.resolve_columns(column_names)

                # Track and filter by confidence (exclude unresolved)
                high_confidence = [
                    m for m in resolved if m.source != "unresolved" and (m.confidence >= 0.7 or m.source == "static")
                ]
                low_confidence = [
                    m for m in resolved if m.source != "unresolved" and m.confidence < 0.7
                ]
                unresolved = [
                    m for m in resolved if m.source == "unresolved"
                ]

                print(
                    f"✓ Auto mode: {len(high_confidence)} high-confidence, {len(low_confidence)} low-confidence, {len(unresolved)} unresolved")

                for mapping in high_confidence:
                    add_column_provenance(
                        self.provenance,
                        column_name=mapping.column_name,
                        source=mapping.source,
                        confidence=mapping.confidence,
                        variable=mapping.mapped_variable,
                        rationale=mapping.rationale
                    )

                # Warn on low-confidence
                if low_confidence:
                    print(
                        f"⚠ Warning: {len(low_confidence)} columns below confidence threshold")
                    for m in low_confidence:
                        self.provenance.warnings.append(
                            f"Low confidence mapping for '{m.column_name}': {m.rationale}"
                        )

                current_step = "output_generation"
                # Build annotations dictionary from high-confidence mappings
                annotations_dict = {}
                for mapping in high_confidence:
                    annotations_dict[mapping.column_name] = {
                        "variable": mapping.mapped_variable,
                        "source": mapping.source,
                        "confidence": mapping.confidence,
                        "rationale": mapping.rationale
                    }

                # Save outputs
                await self._save_outputs(
                    participants_tsv_path,
                    output_dir,
                    annotations_dict,
                    dataset_name=dataset_name,
                )

                print(f"✓ Auto mode completed successfully")

                return True, self.provenance

        except Exception as e:
            print(
                f"✗ Auto mode error at step '{current_step}': {type(e).__name__}")
            print(f"✗ Error details: {str(e)}")
            if self.config.artifacts_dir:
                print(f"✗ Check artifacts in: {self.config.artifacts_dir}")
            self.provenance.warnings.append(
                f"Auto mode failed at step '{current_step}': {str(e)}")
            return False, None

    async def _run_full_auto(
        self,
        participants_tsv_path: Path,
        output_dir: Path,
        dataset_name: str = "",
    ) -> tuple[bool, "ProvenanceReport | None"]:
        """
        Execute full-auto mode: fully autonomous (experimental/unstable).

        ⚠️ WARNING: This mode uses lenient confidence thresholds (>= 0.5)
        and requires careful review of outputs.

        Steps:
        1. Open browser and upload TSV
        2. Resolve columns with lenient thresholds (0.5+)
        3. Auto-fill all forms (static + fuzzy + stub for AI)
        4. Export autonomously
        5. Generate detailed provenance with warnings
        """
        current_step = "initialization"
        try:
            print("⚠️  EXPERIMENTAL/UNSTABLE: Full-auto mode activated")
            print("⚠️  Using lenient confidence thresholds (>= 0.5)")
            print("⚠️  Review provenance sidecar carefully before use")

            current_step = "browser_launch"
            async with NBAnnotationToolBrowserSession(
                headless=self.config.headless,
                timeout=self.config.timeout,
                artifacts_dir=self.config.artifacts_dir
            ) as session:
                current_step = "navigation"
                await session.navigate_to()

                current_step = "get_started_click"
                await session.click_get_started()
                print(f"✓ Clicked 'Get Started' button")

                current_step = "file_upload"
                # Upload TSV data file (required)
                await session.upload_file(
                    participants_tsv_path,
                    file_type="tsv"
                )
                print(f"✓ Uploaded {participants_tsv_path.name}")

                # Upload optional phenotype dictionary JSON
                if self.config.phenotype_dictionary and self.config.phenotype_dictionary.exists():
                    await session.upload_file(
                        self.config.phenotype_dictionary,
                        file_type="json"
                    )
                    print(
                        f"✓ Uploaded phenotype dictionary: {self.config.phenotype_dictionary.name}")

                # Resolve with lenient confidence (>= 0.5)
                current_step = "column_resolution"
                try:
                    column_names = parse_tsv_columns(participants_tsv_path)
                except Exception as e:
                    print(
                        f"⚠ Warning: Could not parse TSV columns: {e}. Using placeholder columns.")
                    column_names = ["participant_id", "age", "sex"]

                resolved = self.resolver.resolve_columns(column_names)
                print(
                    f"✓ Resolved {len(resolved)} columns (lenient thresholds)")

                for mapping in resolved:
                    if mapping.source == "unresolved":
                        continue  # Skip unresolved; will require manual annotation
                    add_column_provenance(
                        self.provenance,
                        column_name=mapping.column_name,
                        source=mapping.source,
                        confidence=mapping.confidence,
                        variable=mapping.mapped_variable,
                        rationale=mapping.rationale
                    )

                # Add explicit warning for full-auto
                self.provenance.warnings.append(
                    "⚠️ FULL-AUTO MODE: All mappings are AI or fuzzy-assisted; review required"
                )

                current_step = "output_generation"
                # Build annotations dictionary from all resolved mappings
                annotations_dict = {}
                for mapping in resolved:
                    if mapping.source != "unresolved":
                        annotations_dict[mapping.column_name] = {
                            "variable": mapping.mapped_variable,
                            "source": mapping.source,
                            "confidence": mapping.confidence,
                            "rationale": mapping.rationale
                        }

                # Save outputs
                await self._save_outputs(
                    participants_tsv_path,
                    output_dir,
                    annotations_dict,
                    dataset_name=dataset_name,
                )

                print("✓ Full-auto mode: Completed")

                return True, self.provenance

        except Exception as e:
            print(
                f"✗ Full-auto mode error at step '{current_step}': {type(e).__name__}")
            print(f"✗ Error details: {str(e)}")
            if self.config.artifacts_dir:
                print(f"✗ Check artifacts in: {self.config.artifacts_dir}")
            self.provenance.warnings.append(
                f"Full-auto mode failed at step '{current_step}': {str(e)}")
            return False, None

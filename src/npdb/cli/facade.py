"""
Facade Pattern — high-level entry points for dataset conversion and BIDS
standardization pipelines.

Clients (e.g. the CLI) interact with these two facades rather than with the
managers, annotators, and converters directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from npdb.annotation import AnnotationConfig
from npdb.managers.annotation import BIDSStandardizer, NeurobagelAnnotator
from npdb.managers.neuropoly import BagelNeuroPolyMTL
from npdb.report import LedgerObserver, RunLedger


class DatasetConversionFacade:
    """
    Facade for the gitea2bagel pipeline.

    Orchestrates:
    1. Clone a dataset from DataNeuroPolyMTL
    2. Locate participants.tsv
    3. Run NeurobagelAnnotator (with an optional LedgerObserver)
    4. Convert BIDS data via BagelNeuroPolyMTL.convert_bids
    """

    def __init__(
        self,
        annotation_config: AnnotationConfig,
        run_ledger: Optional[RunLedger] = None,
    ) -> None:
        self._annotation_config = annotation_config
        self._run_ledger = run_ledger or RunLedger()

    async def run(
        self,
        dataset_dir: Path,
        output: Path,
        extend_modalities: bool = False,
    ) -> None:
        """
        Execute the full gitea → Neurobagel JSON-LD conversion for *dataset*.

        Args:
            dataset_dir:  Local path to the cloned dataset.
            output:   Directory where JSON-LD output and provenance are written.
            extend_modalities: Enable custom modality suffix mapping during
                BIDS preflight checks.
        """
        output.mkdir(parents=True, exist_ok=True)

        # Locate dataset_description.json
        dataset_description_path = dataset_dir / "dataset_description.json"
        if not dataset_description_path.exists():
            self._run_ledger.record_failure(
                f"dataset_description.json not found in {dataset_dir}"
            )
            self._run_ledger.flush()
            raise FileNotFoundError(
                f"dataset_description.json not found in cloned dataset: {dataset_dir}"
            )

        with open(dataset_description_path, "r", encoding="utf-8") as f:
            dataset_description = json.load(f)

        # Locate participants.tsv
        participants_tsv_path = dataset_dir / "participants.tsv"
        if not participants_tsv_path.exists():
            self._run_ledger.record_failure(
                f"participants.tsv not found in {dataset_dir}"
            )
            self._run_ledger.flush()
            raise FileNotFoundError(
                f"participants.tsv not found in cloned dataset: {dataset_dir}"
            )

        # Annotate
        annotator = NeurobagelAnnotator(self._annotation_config)
        annotator.add_observer(LedgerObserver(self._run_ledger))

        success = await annotator.execute(
            input_path=participants_tsv_path,
            output_dir=output,
        )

        if not success:
            self._run_ledger.record_failure("Annotation step returned False")
            self._run_ledger.flush()
            return

        # Convert BIDS
        bagel_manager = BagelNeuroPolyMTL(str(output))
        phenotypes_tsv = str(output / "phenotypes.tsv")
        phenotypes_annotations = str(output / "phenotypes_annotations.json")
        bagel_manager.convert_bids(
            dataset=dataset_dir.name,
            bids_dir=str(dataset_dir),
            phenotypes_tsv=phenotypes_tsv,
            phenotypes_annotations=phenotypes_annotations,
            dataset_description=dataset_description,
            extend_modalities=extend_modalities,
        )

        self._run_ledger.record_success()
        self._run_ledger.flush()


class BIDSStandardizationFacade:
    """
    Facade for the BIDS standardization pipeline.

    Orchestrates:
    1. Validate that participants.tsv exists
    2. Run BIDSStandardizer (with an optional LedgerObserver)
    """

    def __init__(
        self,
        annotation_config: AnnotationConfig,
        run_ledger: Optional[RunLedger] = None,
    ) -> None:
        self._annotation_config = annotation_config
        self._run_ledger = run_ledger or RunLedger()

    async def run(self, bids_dir: Path) -> None:
        """
        Standardize a BIDS dataset rooted at *bids_dir*.

        Args:
            bids_dir: Root of the BIDS dataset (must contain participants.tsv).
        """
        participants_tsv = bids_dir / "participants.tsv"
        if not participants_tsv.exists():
            self._run_ledger.record_failure(f"participants.tsv not found in {bids_dir}")
            self._run_ledger.flush()
            raise FileNotFoundError(
                f"participants.tsv not found in BIDS directory: {bids_dir}"
            )

        standardizer = BIDSStandardizer(self._annotation_config)
        standardizer.add_observer(LedgerObserver(self._run_ledger))

        success = await standardizer.execute(input_path=bids_dir)

        if success:
            self._run_ledger.record_success()
        else:
            self._run_ledger.record_failure("BIDSStandardizer returned False")

        self._run_ledger.flush()

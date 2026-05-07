"""
Facade Pattern — high-level entry points for dataset conversion and BIDS
standardization pipelines.

Clients (e.g. the CLI) interact with these two facades rather than with the
managers, annotators, and converters directly.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from npdb.annotation import AnnotationConfig
from npdb.managers.annotation import BIDSStandardizer, NeurobagelAnnotator
from npdb.managers.neuropoly import BagelNeuroPolyMTL, DataNeuroPolyMTL
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
        gitea_manager: DataNeuroPolyMTL,
        annotation_config: AnnotationConfig,
        run_ledger: Optional[RunLedger] = None,
    ) -> None:
        self._neurogitea = gitea_manager
        self._annotation_config = annotation_config
        self._run_ledger = run_ledger or RunLedger()

    async def run(self, dataset: str, output: Path) -> None:
        """
        Execute the full gitea → Neurobagel JSON-LD conversion for *dataset*.

        Args:
            dataset:  Repository name on DataNeuroPolyMTL (e.g. "my-dataset").
            output:   Directory where JSON-LD output and provenance are written.
        """
        output.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="npdb_clone_") as tmp_dir:
            local_clone = str(Path(tmp_dir) / dataset)

            # 1. Clone the repository
            self._neurogitea.clone_repository(dataset, local_clone, light=True)

            # 2. Extend dataset description (stub on base class; may be
            #    overridden by DataNeuroPolyMTL subclasses in future)
            dataset_description = self._neurogitea.extend_description(
                dataset, local_clone
            )

            # 3. Locate participants.tsv
            participants_tsv_path = Path(local_clone) / "participants.tsv"
            if not participants_tsv_path.exists():
                self._run_ledger.record_failure(
                    f"participants.tsv not found in {local_clone}"
                )
                self._run_ledger.flush()
                raise FileNotFoundError(
                    f"participants.tsv not found in cloned dataset: {local_clone}"
                )

            # 4. Annotate
            annotator = NeurobagelAnnotator(self._annotation_config)
            annotator.add_observer(LedgerObserver(self._run_ledger))

            success = await annotator.execute(
                participants_tsv_path=participants_tsv_path,
                output_dir=output,
            )

            if not success:
                self._run_ledger.record_failure("Annotation step returned False")
                self._run_ledger.flush()
                return

            # 5. Convert BIDS
            bagel_manager = BagelNeuroPolyMTL(str(output))
            phenotypes_tsv = str(output / "phenotypes.tsv")
            phenotypes_annotations = str(output / "phenotypes_annotations.json")
            bagel_manager.convert_bids(
                dataset=dataset,
                bids_dir=local_clone,
                phenotypes_tsv=phenotypes_tsv,
                phenotypes_annotations=phenotypes_annotations,
                dataset_description=dataset_description,
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

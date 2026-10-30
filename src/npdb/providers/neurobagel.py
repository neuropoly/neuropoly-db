from pathlib import Path
from unittest.mock import patch

from bagel.cli import bagel
from typer.testing import CliRunner

from bids_repair.validation import BidsSeverity, validate_and_repair_bids_dataset
from npdb.external.neurobagel.errors import BagelCLIError
from npdb.providers.model import BagelDB


class BagelMixin:
    def __init__(self, db: BagelDB):
        self.cli = CliRunner()
        self.db = db

    def bids2tsv(
        self,
        bids_directory: str,
        output_tsv: str,
        validation_report: dict | None = None,
        severity_threshold: BidsSeverity | str = BidsSeverity.WARNING,
    ):
        report = (
            validation_report
            if validation_report is not None
            else validate_and_repair_bids_dataset(
                bids_directory, severity_threshold=severity_threshold
            )
        )
        repair_details = report.get("repair", {})
        report = repair_details.get("final_report", report)

        with patch("bagel.cli.BIDSLayout", return_value=report):
            self._run_bagel_cli(
                "bids2tsv",
                "--bids-dir",
                bids_directory,
                "--output",
                output_tsv,
                "--overwrite",
            )

    def bagel_pheno(
        self,
        dataset_name: str,
        phenotypes_tsv: str,
        phenotypes_annotations: str,
        dataset_description: str,
    ):
        self._run_bagel_cli(
            "pheno",
            "--pheno",
            phenotypes_tsv,
            "--dictionary",
            phenotypes_annotations,
            "--dataset-description",
            dataset_description,
            "--output",
            str(Path(self.db.root) / f"{dataset_name}.jsonld"),
            "--overwrite",
        )

    def bagel_bids(self, dataset_name: str, bids_table: str):
        jsonld_path = str(Path(self.db.root) / f"{dataset_name}.jsonld")
        self._run_bagel_cli(
            "bids",
            "--jsonld-path",
            jsonld_path,
            "--bids-table",
            bids_table,
            "--output",
            jsonld_path,
            "--overwrite",
        )

    def _run_bagel_cli(self, *args):
        result = self.cli.invoke(bagel, args)
        if result.exit_code != 0:
            output = result.output
            if result.exception is not None and not isinstance(
                result.exception, SystemExit
            ):
                output += (
                    f"\nBagel exception: {type(result.exception).__name__}: "
                    f"{result.exception}\n"
                )
            raise BagelCLIError.from_result(
                command=" ".join(str(a) for a in args),
                exit_code=result.exit_code,
                output=output,
            ) from result.exception

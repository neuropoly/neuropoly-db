
import os
from typer.testing import CliRunner
from bagel.cli import bagel

from npdb.managers.base import DBManager


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
        result = self.cli.invoke(bagel, args)
        if result.exit_code != 0:
            raise RuntimeError(
                f"Bagel CLI failed with exit code {result.exit_code} and output: {result.output}")


class NeurobagelManager(DBManager):
    def __init__(self, jsonld: str):
        self.db = BagelDB(jsonld)

    @property
    def datasets(self):
        return os.listdir(self.db.root)

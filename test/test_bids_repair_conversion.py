import json
from unittest.mock import patch

import pytest

from npdb.annotation import AnnotationConfig
from npdb.cli.facade import DatasetConversionFacade
from npdb.managers.neuropoly import BagelNeuroPolyMTL


@pytest.mark.asyncio
async def test_facade_validates_before_parsing_dataset_description(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "dataset_description.json").write_text("{", encoding="utf-8")
    facade = DatasetConversionFacade(AnnotationConfig(mode="manual"))

    with (
        patch(
            "npdb.cli.facade.validate_and_repair_bids_dataset",
            side_effect=RuntimeError("validator report retained"),
        ) as validate,
        pytest.raises(RuntimeError, match="validator report retained"),
    ):
        await facade.run(
            dataset,
            tmp_path / "output",
            bids_version="1.9.0",
            suffix_aliases={"FA": "T1w"},
            severity_threshold="error",
        )

    validate.assert_called_once_with(
        dataset,
        bids_version="1.9.0",
        suffix_aliases={"FA": "T1w"},
        severity_threshold="error",
    )


def test_conversion_repairs_before_phenotype_and_suffix_preflight(tmp_path):
    bids_dir = tmp_path / "dataset"
    bids_dir.mkdir()
    description = {"Name": "before"}
    description_path = bids_dir / "dataset_description.json"
    description_path.write_text(json.dumps(description), encoding="utf-8")
    manager = BagelNeuroPolyMTL(str(tmp_path / "output"))
    report = {"datasets": [{"errors": 0}], "repair": {"applied": [], "unresolved": []}}

    class StopAtPreprocess(Exception):
        pass

    with (
        patch(
            "npdb.managers.neuropoly.validate_and_repair_bids_dataset",
            return_value=report,
        ) as validate,
        patch.object(
            manager,
            "_preprocess_phenotypes",
            side_effect=StopAtPreprocess,
        ) as preprocess,
        patch.object(manager, "_run_preflight") as preflight,
        pytest.raises(StopAtPreprocess),
    ):
        manager.convert_bids(
            dataset="dataset",
            bids_dir=str(bids_dir),
            phenotypes_tsv=str(tmp_path / "phenotypes.tsv"),
            phenotypes_annotations=str(tmp_path / "annotations.json"),
            dataset_description=description,
            bids_version="1.9.0",
            suffix_aliases={"FA": "T1w"},
            severity_threshold="info",
        )

    validate.assert_called_once_with(
        str(bids_dir),
        bids_version="1.9.0",
        suffix_aliases={"FA": "T1w"},
        severity_threshold="info",
    )
    preprocess.assert_called_once()
    preflight.assert_not_called()

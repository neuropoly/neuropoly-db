import json
from inspect import signature
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from bagel import mappings
from bagel.cli import pheno
from bagel.utilities import pheno_utils

from npdb.annotation.autofix import (
    fix_missing_levels,
    fix_trailing_empty_tsv_cells,
    strip_tsv_cell_whitespace,
)
from npdb.cli.errors import diagnose
from npdb.external.neurobagel.errors import BagelCLIError
from npdb.external.neurobagel.schema import convert_to_bagel_schema
from npdb.managers.neuropoly import BagelNeuroPolyMTL


@pytest.mark.parametrize("trailing_tabs", [False, True])
def test_generated_annotations_and_tsv_produce_real_bagel_jsonld(
    tmp_path, trailing_tabs
):
    output = tmp_path / "output"
    output.mkdir()
    manager = BagelNeuroPolyMTL(str(output))
    tsv = output / "phenotypes.tsv"
    annotations = output / "phenotypes_annotations.json"
    end = "\t" if trailing_tabs else ""
    tsv.write_text(
        f"id\tsex\tage\nsub-01\tF\t24{end}\nsub-02 \tF \t30\nsub-03\tM\t28{end}\n"
    )
    mappings_dict = {
        "mappings": {
            "id": {"variable": "nb:ParticipantID", "variableType": "Identifier"},
            "sex": {
                "variable": "nb:Sex",
                "variableType": "Categorical",
                "levels": {
                    "F": {"termURL": "ncit:C16576", "label": "Female"},
                    "M": {"termURL": "ncit:C20197", "label": "Male"},
                },
            },
            "age": {
                "variable": "nb:Age",
                "variableType": "Continuous",
                "format": "nb:FromFloat",
            },
        }
    }
    dictionary = convert_to_bagel_schema(
        {
            key: {"variable": value["variable"]}
            for key, value in mappings_dict["mappings"].items()
        },
        mappings_dict,
    )
    annotations.write_text(json.dumps(dictionary))
    description = tmp_path / "dataset_description.json"
    description.write_text(
        json.dumps({"Name": "Synthetic public dataset", "AccessType": "public"})
    )
    manager._preprocess_phenotypes(tsv, annotations, {})
    namespaces = [
        {
            "config_name": "Neurobagel",
            "namespaces": {
                "standard": [
                    {
                        "namespace_prefix": "nb",
                        "namespace_url": "http://neurobagel.org/vocab/",
                    },
                    {
                        "namespace_prefix": "ncit",
                        "namespace_url": (
                            "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#"
                        ),
                    },
                ]
            },
        }
    ]
    choices = signature(pheno).parameters["config"].default.click_type
    with (
        patch.object(mappings, "CONFIG_NAMESPACES_MAPPING", namespaces),
        patch.object(mappings, "CONFIG_NAMESPACES_FETCHING_ERR", None),
        patch.object(choices, "choices", ("Neurobagel",)),
        patch(
            "bagel.utilities.bids_utils.get_bids_suffix_to_std_term_mapping",
            return_value={"T2w": "nidm:T2Weighted"},
        ),
    ):
        manager.bagel_pheno("synthetic", str(tsv), str(annotations), str(description))
        bids_table = tmp_path / "bids.tsv"
        bids_table.write_text(
            "sub\tses\tsuffix\tpath\n"
            + "".join(
                f"{sub}\tn/a\tT2w\t/data/{sub}/anat/{sub}_T2w.nii.gz\n"
                for sub in ("sub-01", "sub-02", "sub-03")
            )
        )
        manager.bagel_bids("synthetic", str(bids_table))
    graph = json.loads((output / "synthetic.jsonld").read_text())
    assert [sample["hasLabel"] for sample in graph["hasSamples"]] == [
        "sub-01", "sub-02", "sub-03",
    ]
    assert all(len(sample["hasSession"]) == 2 for sample in graph["hasSamples"])
    sessions = [
        next(s for s in sample["hasSession"] if s["schemaKey"] == "PhenotypicSession")
        for sample in graph["hasSamples"]
    ]
    assert [session["hasAge"] for session in sessions] == [24.0, 30.0, 28.0]
    assert [session["hasSex"]["identifier"] for session in sessions] == [
        "ncit:C16576", "ncit:C16576", "ncit:C20197",
    ]
    assert pheno_utils.categorical_cols_have_bids_levels(
        json.loads(annotations.read_text())
    )


@pytest.mark.parametrize("values", ["F", "n/a"])
@pytest.mark.parametrize("existing_levels", [None, {"M": "Existing description"}])
def test_existing_semantic_levels_backfill_bids_levels_idempotently(
    tmp_path, values, existing_levels
):
    tsv = tmp_path / "participants.tsv"
    tsv.write_text(f"sex\n{values}\n")
    annotations = tmp_path / "annotations.json"
    semantic = {
        "F": {"TermURL": "ncit:C16576", "Label": "Female"},
        "M": {"TermURL": "ncit:C20197", "Label": "Male"},
    }
    dictionary = {
        "sex": {
            "Annotations": {
                "VariableType": "Categorical",
                "Levels": semantic,
                "MissingValues": ["n/a"],
            },
        },
    }
    if existing_levels is not None:
        dictionary["sex"]["Levels"] = existing_levels
    annotations.write_text(json.dumps(dictionary))
    assert fix_missing_levels(tsv, annotations)
    data = json.loads(annotations.read_text())
    assert data["sex"]["Levels"] == {
        "F": "Female", "M": "Male", **(existing_levels or {}),
    }
    assert data["sex"]["Annotations"]["Levels"] == semantic
    original = annotations.read_bytes()
    assert fix_missing_levels(tsv, annotations) == []
    assert annotations.read_bytes() == original


def test_all_missing_categorical_values_get_empty_bids_levels(tmp_path):
    tsv = tmp_path / "participants.tsv"
    tsv.write_text("sex\nn/a\n")
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({
        "sex": {"Annotations": {
            "VariableType": "Categorical", "Levels": {}, "MissingValues": ["n/a"],
        }},
    }))
    assert fix_missing_levels(tsv, annotations)
    assert json.loads(annotations.read_text())["sex"]["Levels"] == {}
    assert fix_missing_levels(tsv, annotations) == []


def test_extra_empty_tsv_cells_are_removed_without_changing_quoted_values(tmp_path):
    tsv = tmp_path / "participants.tsv"
    tsv.write_text('id\tnote\nsub-01\t"quoted\tvalue"\t\t\nsub-02\t\t\n')
    assert fix_trailing_empty_tsv_cells(tsv)
    assert tsv.read_text() == 'id\tnote\nsub-01\t"quoted\tvalue"\nsub-02\t\n'
    assert fix_trailing_empty_tsv_cells(tsv) == []


@pytest.mark.parametrize("extra", ["real data", " "])
def test_nonempty_extra_cells_fail_without_modifying_input(tmp_path, extra):
    tsv = tmp_path / "participants.tsv"
    tsv.write_text(f"id\tsex\nsub-01\tF\t\nsub-02\tM\t{extra}\n")
    original = tsv.read_bytes()
    with pytest.raises(ValueError, match="row 3.*refusing to discard data"):
        fix_trailing_empty_tsv_cells(tsv)
    assert tsv.read_bytes() == original


def test_bagel_python_exception_is_reported_and_chained(tmp_path):
    manager = BagelNeuroPolyMTL(str(tmp_path))
    underlying = TypeError('can only concatenate str (not "int") to str')
    manager.cli = Mock()
    manager.cli.invoke.return_value = SimpleNamespace(
        exit_code=1, output="Warning before failure", exception=underlying,
    )
    with pytest.raises(BagelCLIError) as caught:
        manager._run_bagel_cli("pheno")
    assert "Warning before failure" in caught.value.plain_output
    assert "TypeError: can only concatenate" in caught.value.plain_output
    assert caught.value.__cause__ is underlying
    assert "TypeError: can only concatenate" in diagnose(caught.value).message


def test_bagel_normal_exit_preserves_existing_output(tmp_path):
    manager = BagelNeuroPolyMTL(str(tmp_path))
    manager.cli = Mock()
    manager.cli.invoke.return_value = SimpleNamespace(
        exit_code=1, output="Invalid input", exception=SystemExit(1),
    )
    with pytest.raises(BagelCLIError) as caught:
        manager._run_bagel_cli("pheno")
    assert caught.value.plain_output == "Invalid input"


def test_cell_whitespace_is_trimmed_without_changing_inner_content(tmp_path):
    tsv = tmp_path / "participants.tsv"
    tsv.write_text(" id\tsex \tnote\nsub-06 \t F\tleft  arm\nsub-07\tM\t\n")
    warnings = strip_tsv_cell_whitespace(tsv)
    assert len(warnings) == 1
    assert "'sub-06 ' → 'sub-06'" in warnings[0]
    assert tsv.read_text() == "id\tsex\tnote\nsub-06\tF\tleft  arm\nsub-07\tM\t\n"
    assert strip_tsv_cell_whitespace(tsv) == []


def test_clean_tsv_is_not_rewritten(tmp_path):
    tsv = tmp_path / "participants.tsv"
    tsv.write_bytes(b"id\tsex\r\nsub-01\tF\r\n")
    assert strip_tsv_cell_whitespace(tsv) == []
    assert tsv.read_bytes() == b"id\tsex\r\nsub-01\tF\r\n"

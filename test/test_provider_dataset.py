import importlib
import io
import zipfile
from unittest.mock import Mock, patch

import pytest
from typer.testing import CliRunner

from npdb.cli.helpers import prepare_provider_dataset
from npdb.managers.figshare import FigshareProviderManager
from npdb.managers.model import ProviderManager, ProviderName


def test_reorganizes_dataset_and_generates_participants(tmp_path):
    rawdata = tmp_path / "rawdata"
    rawdata.mkdir()
    for name in ("sub-02_ses-1_T1w.nii.gz", "sub-01_T2w.nii.gz", "sub-02_T2w.json"):
        (rawdata / name).write_bytes(b"image")
    for name in ("models", "markers"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "sub-99_model.nii.gz").write_bytes(b"derived")
    (tmp_path / ".git").mkdir()
    (tmp_path / "README").write_text("readme")

    assert prepare_provider_dataset(tmp_path) == tmp_path
    assert not rawdata.exists()
    assert (tmp_path / "derivatives/models/sub-99_model.nii.gz").exists()
    assert (tmp_path / "derivatives/markers").is_dir()
    assert (tmp_path / ".git").is_dir()
    assert (tmp_path / "README").read_text() == "readme"
    assert (tmp_path / "participants.tsv").read_text() == (
        "participant_id\tage\tsex\nsub-01\tN/A\tN/A\nsub-02\tN/A\tN/A\n"
    )


def test_preserves_subject_directories_and_existing_table(tmp_path):
    (tmp_path / "sub-01").mkdir()
    (tmp_path / "analysis").mkdir()
    participants = tmp_path / "participants.tsv"
    participants.write_text("participant_id\tage\tsex\nsub-01\t30\tF\n")
    original = participants.read_bytes()
    prepare_provider_dataset(tmp_path)
    prepare_provider_dataset(tmp_path)
    assert (tmp_path / "sub-01").is_dir()
    assert (tmp_path / "derivatives/analysis").is_dir()
    assert participants.read_bytes() == original


def test_existing_derivatives_prevents_reorganization(tmp_path):
    (tmp_path / "derivatives").mkdir()
    (tmp_path / "analysis").mkdir()
    (tmp_path / "sub-Ab12").mkdir()
    prepare_provider_dataset(tmp_path)
    assert (tmp_path / "analysis").is_dir()
    assert "sub-Ab12\tN/A\tN/A" in (tmp_path / "participants.tsv").read_text()


def test_bids_auxiliary_directories_are_preserved_and_not_subject_sources(tmp_path):
    (tmp_path / "sub-01").mkdir()
    for name in ("sourcedata", "stimuli", "code", "phenotype"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "sub-99_example.txt").write_text("auxiliary")
    (tmp_path / "models").mkdir()
    prepare_provider_dataset(tmp_path)
    for name in ("sourcedata", "stimuli", "code", "phenotype"):
        assert (tmp_path / name / "sub-99_example.txt").read_text() == "auxiliary"
    assert (tmp_path / "derivatives/models").is_dir()
    assert (tmp_path / "participants.tsv").read_text() == (
        "participant_id\tage\tsex\nsub-01\tN/A\tN/A\n"
    )


def test_only_protected_directories_does_not_create_derivatives(tmp_path):
    for name in ("sub-01", "sourcedata", "stimuli", "code", "phenotype"):
        (tmp_path / name).mkdir()
    prepare_provider_dataset(tmp_path)
    assert not (tmp_path / "derivatives").exists()


def test_rawdata_participants_table_is_preserved(tmp_path):
    rawdata = tmp_path / "rawdata"
    rawdata.mkdir()
    (rawdata / "participants.tsv").write_text("existing table")
    prepare_provider_dataset(tmp_path)
    assert (tmp_path / "participants.tsv").read_text() == "existing table"
    assert not rawdata.exists()


def test_collision_prevents_moves(tmp_path):
    rawdata = tmp_path / "rawdata"
    rawdata.mkdir()
    (rawdata / "README").write_text("raw")
    (tmp_path / "README").write_text("root")
    (tmp_path / "analysis").mkdir()
    with pytest.raises(FileExistsError, match="Cannot flatten rawdata"):
        prepare_provider_dataset(tmp_path)
    assert (rawdata / "README").read_text() == "raw"
    assert (tmp_path / "README").read_text() == "root"
    assert (tmp_path / "analysis").is_dir()
    assert not (tmp_path / "derivatives").exists()


def test_missing_subjects_raise_without_empty_table(tmp_path):
    (tmp_path / "derivatives").mkdir()
    (tmp_path / "derivatives/sub-99_T1w.nii.gz").touch()
    (tmp_path / ".sub-01_T1w.nii.gz").touch()
    with pytest.raises(ValueError, match="No BIDS subject labels"):
        prepare_provider_dataset(tmp_path)
    assert not (tmp_path / "participants.tsv").exists()


def test_provider_prepares_extracted_data_before_conversion(tmp_path):
    module = importlib.import_module("npdb.cli.cli")
    manager = Mock()
    manager.provider_name = "figshare"

    def unpack(fetched):
        assert fetched == tmp_path
        (tmp_path / "rawdata").mkdir()
        (tmp_path / "rawdata/sub-01_T1w.nii.gz").touch()
        return tmp_path

    def fetch(*args, **kwargs):
        return ProviderManager().prepare(unpack(tmp_path))

    manager.fetch.side_effect = fetch

    def convert(**kwargs):
        assert kwargs["input_dir"] == tmp_path
        assert not (tmp_path / "rawdata").exists()
        assert (tmp_path / "sub-01_T1w.nii.gz").exists()
        assert (tmp_path / "participants.tsv").read_text() == (
            "participant_id\tage\tsex\nsub-01\tN/A\tN/A\n"
        )

    with (
        patch.object(module.ProviderManagerFactory, "create", return_value=manager),
        patch("npdb.cli.cli.local2bagel", side_effect=convert) as conversion,
    ):
        module._provider_call(
            "figshare",
            "123",
            output=tmp_path / "out",
            online_url="https://example.com",
            access_type="public",
            mode="manual",
            phenotype_dict=None,
            headless=True,
            timeout=300,
            artifacts_dir=None,
            ai_provider=None,
            ai_model=None,
            header_map=None,
            extend_modalities=True,
        )
    conversion.assert_called_once()


def test_figshare_cli_fetches_and_prepares_inside_output(tmp_path):
    module = importlib.import_module("npdb.cli.cli")
    output = tmp_path / "chosen" / "output"
    dataset = output / "figshare_123"
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("rawdata/sub-01_T1w.nii.gz", b"image")
    article = Mock()
    article.json.return_value = {
        "files": [{"name": "rawdata.zip", "download_url": "https://example.com/file"}]
    }
    download = Mock()
    download.content = bundle.getvalue()

    def convert(**kwargs):
        assert kwargs["input_dir"] == dataset
        assert kwargs["output"] == output
        assert (dataset / "sub-01_T1w.nii.gz").read_bytes() == b"image"
        assert (dataset / "participants.tsv").read_text() == (
            "participant_id\tage\tsex\nsub-01\tN/A\tN/A\n"
        )
        assert not (dataset / "rawdata.zip").exists()
        assert not (dataset / "rawdata").exists()
        (output / "neurobagel.jsonld").write_text("converted")

    app = module.npdb
    command = ["convert", "bagel"]
    with (
        patch.object(
            module.ProviderManagerFactory,
            "create",
            return_value=FigshareProviderManager(token="test-token"),
        ),
        patch("npdb.managers.figshare.httpx.get", side_effect=[article, download]),
        patch("npdb.cli.cli.local2bagel", side_effect=convert) as conversion,
    ):
        result = CliRunner().invoke(app, [*command, "figshare", "123", str(output)])

    assert result.exit_code == 0, result.exception
    conversion.assert_called_once()
    assert (output / "neurobagel.jsonld").read_text() == "converted"
    assert not (output.parent / "figshare_123").exists()


def test_other_provider_staging_path_is_unchanged(tmp_path):
    module = importlib.import_module("npdb.cli.cli")
    manager = Mock()
    manager.provider_name = ProviderName.ZENODO
    output = tmp_path / "out"
    staging = output / "zenodo_123"
    manager.fetch.return_value = staging
    with (
        patch.object(module.ProviderManagerFactory, "create", return_value=manager),
        patch("npdb.cli.cli.local2bagel") as conversion,
    ):
        module._provider_call(
            "zenodo",
            "123",
            output=output,
            online_url="https://example.com",
            access_type="public",
            mode="manual",
            phenotype_dict=None,
            headless=True,
            timeout=300,
            artifacts_dir=None,
            ai_provider=None,
            ai_model=None,
            header_map=None,
            extend_modalities=True,
        )
    manager.fetch.assert_called_once_with("123", staging)
    assert conversion.call_args.kwargs["output"] == output

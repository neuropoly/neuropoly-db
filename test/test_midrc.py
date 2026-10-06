import gzip
import hashlib
import io
import json
import sys
import zipfile
from contextlib import closing
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

import httpx
import pytest
from typer.testing import CliRunner

from npdb.cli.cli import npdb
from npdb.factories import ProviderManagerFactory
from npdb.managers.midrc import MIDRCProviderManager
from npdb.managers.preparation import unpack_provider_archives

GUIDS = [
    "dg.MD1R/ee75bf02-33e9-4d89-ba0a-868dd4aec81c",
    "dg.MD1R/c2364376-ff71-409c-ab74-76b3a0c7ec04",
    "dg.MD1R/cb8f608a-f67a-437b-a2e2-85679d13f79e",
    "dg.MD1R/b6d85fe2-5b5c-4ccd-b172-75b37d32ded5",
]
DATASET = "H6K0-A61V"
URL = f"https://data.midrc.org/discovery/{DATASET}/"


@pytest.fixture
def sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "gen3", ModuleType("gen3"))
    clients = {}
    for name, cls in (
        ("auth", "Gen3Auth"),
        ("index", "Gen3Index"),
        ("file", "Gen3File"),
    ):
        module = ModuleType(f"gen3.{name}")
        client = Mock()
        factory = Mock(return_value=client)
        setattr(module, cls, factory)
        monkeypatch.setitem(sys.modules, f"gen3.{name}", module)
        clients[name] = client
        clients[f"{name}_factory"] = factory
    yield clients


@pytest.fixture
def download_only(monkeypatch):
    monkeypatch.setattr(
        MIDRCProviderManager, "prepare_fetched", lambda self, path: path
    )


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.delenv("NP_NPDB_CACHE_DIR", raising=False)
    monkeypatch.delenv("NP_MIDRC_CREDENTIALS", raising=False)
    return MIDRCProviderManager(
        credentials_path="credentials.json", cache_dir=tmp_path / "cache"
    )


def record(name="bundle.zip", content=b"archive"):
    return {
        "file_name": name,
        "size": len(content),
        "hashes": {"sha256": hashlib.sha256(content).hexdigest()},
        "urls": ["s3://bucket/private-object"],
    }


def discovery_response(guids=GUIDS):
    return httpx.Response(
        200,
        json={
            "gen3_discovery": {
                "files_count": "1",
                "data_download_links": [{"guid": guid} for guid in guids],
            }
        },
        request=httpx.Request("GET", f"https://data.midrc.org/mds/metadata/{DATASET}"),
    )


@pytest.mark.parametrize("identifier", [DATASET, URL, URL.rstrip("/")])
def test_resolves_all_discovery_links(manager, identifier):
    with patch(
        "npdb.managers.midrc.httpx.get", return_value=discovery_response()
    ) as get:
        assert manager._resolve(identifier) == GUIDS
    get.assert_called_once_with(
        f"https://data.midrc.org/mds/metadata/{DATASET}", timeout=30
    )
    assert manager.describe(identifier) == (URL, "restricted")


@pytest.mark.parametrize(
    "payload",
    [
        [{"object_id": GUIDS[0]}],
        {"records": [{"did": GUIDS[0]}]},
        {"files": [{"guid": GUIDS[0]}]},
    ],
)
def test_standard_and_legacy_manifests(manager, tmp_path, payload):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload))
    with patch("npdb.managers.midrc.httpx.get") as get:
        assert manager._resolve(str(manifest)) == GUIDS[:1]
    get.assert_not_called()


def test_bare_uuid_is_not_a_dataset(manager):
    assert manager.discovery_id("ee75bf02-33e9-4d89-ba0a-868dd4aec81c") is None
    assert manager._resolve(GUIDS[0]) == GUIDS[:1]


@pytest.mark.parametrize(
    "payload", [[], {}, [None], [{}], [{"guid": ""}], {"files": []}]
)
def test_invalid_manifests_fail(manager, payload):
    with pytest.raises(ValueError):
        manager._manifest_guids(payload)


def test_guids_are_deduplicated(manager):
    assert manager._manifest_guids([{"guid": GUIDS[0]}, {"did": GUIDS[0]}]) == GUIDS[:1]


@pytest.mark.parametrize(
    "identifier",
    [
        "https://elsewhere.example/discovery/ABC-123/",
        "https://data.midrc.org/not-discovery/ABC-123/",
        "https://data.midrc.org/discovery/%2e%2e/",
        "https://data.midrc.org/discovery/ABC-123/?token=secret",
    ],
)
def test_invalid_discovery_urls_fail_before_network(manager, identifier):
    with patch("npdb.managers.midrc.httpx.get") as get:
        with pytest.raises(ValueError):
            manager._resolve(identifier)
    get.assert_not_called()


@pytest.mark.parametrize(
    "payload", [{}, {"gen3_discovery": {}}, {"gen3_discovery": []}]
)
def test_missing_discovery_links_fail(manager, payload):
    response = discovery_response()
    response._content = json.dumps(payload).encode()
    with patch("npdb.managers.midrc.httpx.get", return_value=response):
        with pytest.raises(ValueError):
            manager._resolve(DATASET)


def test_dataset_requires_cache_and_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("NP_NPDB_CACHE_DIR", raising=False)
    monkeypatch.delenv("NP_MIDRC_CREDENTIALS", raising=False)
    with pytest.raises(ValueError, match="credentials"):
        MIDRCProviderManager().fetch(DATASET, tmp_path)
    with pytest.raises(ValueError, match="cache directory"):
        MIDRCProviderManager(credentials_path="creds")._resolve(DATASET)


def test_manifest_missing_file_is_not_a_guid(manager, tmp_path):
    with pytest.raises(FileNotFoundError):
        manager._resolve(str(tmp_path / "missing.json"))


def test_local_manifest_named_like_dataset_takes_precedence(manager, tmp_path):
    manifest = tmp_path / "ABC-123"
    manifest.write_text(json.dumps([{"object_id": GUIDS[0]}]))
    assert manager.discovery_id(str(manifest)) is None
    assert manager._resolve(str(manifest)) == GUIDS[:1]


def test_factory_passes_cache(tmp_path):
    assert (
        ProviderManagerFactory.create("midrc", cache_dir=tmp_path).cache_dir == tmp_path
    )


def setup_download(sdk, records):
    sdk["index"].get_record.side_effect = records
    sdk["file"].get_presigned_url.return_value = {
        "url": "https://storage.example/file?secret=signature"
    }


def response(content=b"archive", status=200):
    return closing(
        httpx.Response(
            status,
            content=content,
            request=httpx.Request(
                "GET", "https://storage.example/file?secret=signature"
            ),
        )
    )


@pytest.mark.parametrize("reuse", ["download", "cached", "staged"])
def test_main_progress_counts_files_and_preparation(
    manager, sdk, tmp_path, download_only, reuse
):
    from npdb.cli.display import RepoDownloadDisplay, StepStatus

    output = tmp_path / "out"
    setup_download(sdk, [record("first.zip"), record("second.zip")] * 2)
    with (
        patch.object(manager, "_resolve", return_value=GUIDS[:2]),
        patch(
            "npdb.managers.midrc.httpx.stream", side_effect=[response(), response()]
        ) as stream,
    ):
        if reuse != "download":
            manager.fetch(DATASET, output)
            if reuse == "cached":
                (output / "first.zip").unlink()
                (output / "second.zip").unlink()
        display = RepoDownloadDisplay()
        manager.add_download_observer(display)
        steps = []
        original_step = display.on_repo_step

        def update(repo, label, completed, total):
            original_step(repo, label, completed, total)
            steps.append((completed, total, display._repos[repo]._step_bar()))

        def prepare(path):
            state = display._repos["midrc"]
            assert state.current_step == "Preparing dataset"
            assert state.step_num == 2
            assert state.total_steps == 3
            assert state.status == StepStatus.RUNNING
            assert all(file.done for file in state.files.values())
            return path

        with (
            patch.object(display, "on_repo_step", side_effect=update),
            patch.object(manager, "prepare_fetched", side_effect=prepare),
        ):
            assert manager.fetch(DATASET, output) == output
    assert [(completed, total) for completed, total, _ in steps] == [
        (0, 3),
        (1, 3),
        (2, 3),
        (3, 3),
    ]
    assert "  0%" in steps[0][2]
    assert "100%" in steps[-1][2]
    assert display._repos["midrc"].status == StepStatus.SUCCESS
    assert stream.call_count == 2


def test_streams_signed_url_creates_nested_files_and_reuses_cache(
    manager, sdk, tmp_path, download_only
):
    data = b"archive"
    metadata = record("nested/bundle.zip", data)
    setup_download(sdk, [metadata, metadata])
    output = tmp_path / "out"
    with patch(
        "npdb.managers.midrc.httpx.stream", return_value=response(data)
    ) as stream:
        assert manager.fetch(GUIDS[0], output) == output
        assert (output / "nested/bundle.zip").read_bytes() == data
        (output / "nested/bundle.zip").unlink()
        manager.fetch(GUIDS[0], output)
    assert stream.call_count == 1
    sdk["file"].get_presigned_url.assert_called_once_with(GUIDS[0])
    assert (output / "nested/bundle.zip").read_bytes() == data
    assert next(manager.cache_dir.rglob("bundle.zip")).read_bytes() == data
    sdk["file_factory"].assert_called_with(manager.endpoint, sdk["auth"])


def test_cache_not_shared_with_staged_file(manager, sdk, tmp_path, download_only):
    setup_download(sdk, [record()])
    output = tmp_path / "out"
    with patch("npdb.managers.midrc.httpx.stream", return_value=response()):
        manager.fetch(GUIDS[0], output)
    cached = next(manager.cache_dir.rglob("bundle.zip"))
    (output / "bundle.zip").write_bytes(b"modified")
    assert cached.read_bytes() == b"archive"


def test_cache_inside_fetch_directory_is_rejected(manager, sdk, tmp_path):
    manager.cache_dir = tmp_path / "out" / "cache"
    with pytest.raises(ValueError, match="overlap"):
        manager.fetch(GUIDS[0], tmp_path / "out")
    sdk["file"].get_presigned_url.assert_not_called()


def test_insufficient_space_fails_before_download(manager, sdk, tmp_path):
    from collections import namedtuple

    setup_download(sdk, [record()])
    usage = namedtuple("Usage", "total used free")(1, 1, 0)
    with patch("npdb.managers.midrc.shutil.disk_usage", return_value=usage):
        with pytest.raises(OSError, match="Insufficient disk space"):
            manager.fetch(GUIDS[0], tmp_path / "out")
    sdk["file"].get_presigned_url.assert_not_called()


def test_corrupt_cache_is_redownloaded(manager, sdk, tmp_path, download_only):
    setup_download(sdk, [record(), record()])
    output = tmp_path / "out"
    with patch(
        "npdb.managers.midrc.httpx.stream", side_effect=[response(), response()]
    ) as stream:
        manager.fetch(GUIDS[0], output)
        next(manager.cache_dir.rglob("bundle.zip")).write_bytes(b"corrupt")
        (output / "bundle.zip").unlink()
        manager.fetch(GUIDS[0], output)
    assert stream.call_count == 2
    assert (output / "bundle.zip").read_bytes() == b"archive"


def test_existing_different_destination_is_not_overwritten(manager, sdk, tmp_path):
    setup_download(sdk, [record()])
    output = tmp_path / "out"
    output.mkdir()
    (output / "bundle.zip").write_bytes(b"other")
    with pytest.raises(FileExistsError, match="different content"):
        manager.fetch(GUIDS[0], output)
    assert (output / "bundle.zip").read_bytes() == b"other"
    sdk["file"].get_presigned_url.assert_not_called()


def test_single_guid_without_cache_downloads_directly(
    tmp_path, sdk, monkeypatch, download_only
):
    monkeypatch.delenv("NP_NPDB_CACHE_DIR", raising=False)
    manager = MIDRCProviderManager(credentials_path="credentials.json")
    setup_download(sdk, [record()])
    with patch("npdb.managers.midrc.httpx.stream", return_value=response()):
        manager.fetch(GUIDS[0], tmp_path / "out")
    assert (tmp_path / "out/bundle.zip").read_bytes() == b"archive"


@pytest.mark.parametrize(
    "name", ["../escape.zip", "/escape", "a/../../escape", "a\\b", "a//b", "a/./b"]
)
def test_unsafe_index_names_fail_before_transfer(manager, sdk, tmp_path, name):
    setup_download(sdk, [record(name)])
    with pytest.raises(ValueError, match="Unsafe"):
        manager.fetch(GUIDS[0], tmp_path / "out")
    sdk["file"].get_presigned_url.assert_not_called()


def test_symlink_output_is_rejected(manager, sdk, tmp_path):
    linked = tmp_path / "linked"
    linked.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        manager.fetch(GUIDS[0], linked)


def test_colliding_names_fail_before_transfer(manager, sdk, tmp_path):
    setup_download(sdk, [record(), record()])
    with patch(
        "npdb.managers.midrc.httpx.get", return_value=discovery_response(GUIDS[:2])
    ):
        with pytest.raises(FileExistsError, match="Conflicting"):
            manager.fetch(DATASET, tmp_path / "out")
    sdk["file"].get_presigned_url.assert_not_called()


@pytest.mark.parametrize("data", [b"bad", b"corrupt"])
def test_integrity_failure_removes_partial_file(manager, sdk, tmp_path, data):
    setup_download(sdk, [record()])
    with patch("npdb.managers.midrc.httpx.stream", return_value=response(data)):
        with pytest.raises(ValueError, match="size/checksum"):
            manager.fetch(GUIDS[0], tmp_path / "out")
    assert not list(manager.cache_dir.rglob("*.part"))
    assert not list(manager.cache_dir.rglob("bundle.zip"))


def test_oversized_download_stops_and_removes_partial(manager, sdk, tmp_path):
    setup_download(sdk, [record()])
    with patch(
        "npdb.managers.midrc.httpx.stream", return_value=response(b"oversized archive")
    ):
        with pytest.raises(ValueError, match="exceeds indexed size"):
            manager.fetch(GUIDS[0], tmp_path / "out")
    assert not list(manager.cache_dir.rglob("*.part"))


def test_storage_errors_do_not_expose_signed_url(manager, sdk, tmp_path):
    setup_download(sdk, [record()])
    with patch(
        "npdb.managers.midrc.httpx.stream", return_value=response(status=403)
    ) as stream:
        with pytest.raises(RuntimeError, match="storage download failed") as error:
            manager.fetch(GUIDS[0], tmp_path / "out")
    assert "signature" not in str(error.value)
    assert stream.call_count == 1


def test_expired_storage_url_is_renewed_once(manager, sdk, tmp_path, download_only):
    setup_download(sdk, [record()])
    expired = response(b"<Error><Code>RequestExpired</Code></Error>", status=403)
    with patch("npdb.managers.midrc.httpx.stream", side_effect=[expired, response()]):
        manager.fetch(GUIDS[0], tmp_path / "out")
    assert sdk["file"].get_presigned_url.call_count == 2


def test_expired_url_retry_is_bounded(manager, sdk, tmp_path):
    setup_download(sdk, [record()])
    expired = b"<Error><Code>ExpiredToken</Code></Error>"
    with patch(
        "npdb.managers.midrc.httpx.stream",
        side_effect=[
            response(expired, status=403),
            response(expired, status=403),
        ],
    ):
        with pytest.raises(RuntimeError, match="storage download failed"):
            manager.fetch(GUIDS[0], tmp_path / "out")
    assert sdk["file"].get_presigned_url.call_count == 2


@pytest.mark.parametrize("signed", [None, {}, {"url": "s3://bucket/file"}, {"url": 1}])
def test_invalid_signed_response(manager, sdk, tmp_path, signed):
    setup_download(sdk, [record()])
    sdk["file"].get_presigned_url.return_value = signed
    with pytest.raises(ValueError, match="signed"):
        manager.fetch(GUIDS[0], tmp_path / "out")


def test_authorization_error_is_explicit(manager, sdk, tmp_path):
    from requests import HTTPError, Response

    setup_download(sdk, [record()])
    unauthorized = Response()
    unauthorized.status_code = 401
    sdk["file"].get_presigned_url.side_effect = HTTPError(response=unauthorized)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        manager.fetch(GUIDS[0], tmp_path / "out")


def bundle(supported=True):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        if supported:
            archive.writestr("rawdata/sub-01/anat/sub-01_T2w.nii.gz", b"image")
            archive.writestr(
                "rawdata/participants.tsv", "participant_id\tage\tsex\nsub-01\t42\tF\n"
            )
            archive.writestr("masks/sub-01_mask.nii.gz", b"mask")
        else:
            archive.writestr("original/patient/image.dcm", b"dicom")
    return output.getvalue()


@pytest.mark.parametrize("supported", [True, False])
def test_cli_uses_shared_preparation_and_canonical_url(
    manager, sdk, tmp_path, supported
):
    data = bundle(supported)
    setup_download(sdk, [record(content=data)])
    output = tmp_path / "output"

    def convert(**kwargs):
        root = output / f"midrc_{DATASET}"
        assert kwargs["input_dir"] == root
        assert kwargs["online_url"] == URL
        assert kwargs["access_type"] == "restricted"
        assert (root / "sub-01/anat/sub-01_T2w.nii.gz").read_bytes() == b"image"
        assert (root / "derivatives/masks/sub-01_mask.nii.gz").read_bytes() == b"mask"
        assert (root / "participants.tsv").read_text().endswith("sub-01\t42\tF\n")
        assert not (root / "bundle.zip").exists()
        assert next(manager.cache_dir.rglob("bundle.zip")).read_bytes() == data

    with (
        patch("npdb.cli.cli.ProviderManagerFactory.create", return_value=manager),
        patch(
            "npdb.managers.midrc.httpx.get", return_value=discovery_response(GUIDS[:1])
        ),
        patch("npdb.managers.midrc.httpx.stream", return_value=response(data)),
        patch("npdb.cli.cli.local2bagel", side_effect=convert) as conversion,
    ):
        result = CliRunner().invoke(
            npdb,
            [
                "convert",
                "bagel",
                "midrc",
                URL,
                str(output),
                "--cache-dir",
                str(manager.cache_dir),
            ],
        )
    if supported:
        assert result.exit_code == 0, result.exception
        conversion.assert_called_once()
    else:
        assert result.exit_code != 0
        assert "No BIDS subject labels" in str(result.exception)
        conversion.assert_not_called()


def test_extraction_retains_cached_archives(manager, sdk, tmp_path):
    data = bundle()
    setup_download(sdk, [record(content=data)])
    with patch("npdb.managers.midrc.httpx.stream", return_value=response(data)):
        root = manager.fetch(GUIDS[0], tmp_path / "out")
    unpack_provider_archives(root)
    assert not (root / "bundle.zip").exists()
    assert next(manager.cache_dir.rglob("bundle.zip")).read_bytes() == data


def test_shared_pipeline_produces_real_neurobagel_jsonld(manager, sdk, tmp_path):
    import nibabel as nib
    import numpy as np
    from bagel import mappings

    from npdb.managers.annotation import NeurobagelAnnotator

    archive_bytes = io.BytesIO()
    image = nib.Nifti1Image(np.zeros((2, 2, 2), dtype=np.int16), np.eye(4))
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr(
            "rawdata/sub-01/anat/sub-01_T2w.nii.gz", gzip.compress(image.to_bytes())
        )
        archive.writestr(
            "rawdata/participants.tsv", "participant_id\tage\tsex\nsub-01\t42\tF\n"
        )
        archive.writestr(
            "rawdata/dataset_description.json",
            json.dumps(
                {
                    "Name": "Synthetic dataset",
                    "BIDSVersion": "1.9.0",
                    "RepositoryURL": "https://old.example/",
                }
            ),
        )
    data = archive_bytes.getvalue()
    setup_download(sdk, [record(content=data)])
    output = tmp_path / "output"
    dictionary = {
        "participant_id": {
            "Description": "Participant",
            "Annotations": {
                "IsAbout": {"TermURL": "nb:ParticipantID", "Label": "Participant"},
                "VariableType": "Identifier",
            },
        },
        "age": {
            "Description": "Age",
            "Annotations": {
                "IsAbout": {"TermURL": "nb:Age", "Label": "Age"},
                "VariableType": "Continuous",
                "Format": {"TermURL": "nb:FromFloat", "Label": "Decimal years"},
                "MissingValues": ["N/A"],
            },
        },
        "sex": {
            "Description": "Sex",
            "Levels": {"F": "Female"},
            "Annotations": {
                "IsAbout": {"TermURL": "nb:Sex", "Label": "Sex"},
                "VariableType": "Categorical",
                "Levels": {"F": {"TermURL": "ncit:C16576", "Label": "Female"}},
                "MissingValues": ["N/A"],
            },
        },
    }

    async def annotate(*, input_path, output_dir):
        (output_dir / "phenotypes.tsv").write_bytes(input_path.read_bytes())
        (output_dir / "phenotypes_annotations.json").write_text(json.dumps(dictionary))
        return True

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
                        "namespace_url": "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#",
                    },
                    {
                        "namespace_prefix": "nidm",
                        "namespace_url": "http://purl.org/nidash/nidm#",
                    },
                ]
            },
        }
    ]
    with (
        patch("npdb.cli.cli.ProviderManagerFactory.create", return_value=manager),
        patch(
            "npdb.managers.midrc.httpx.get", return_value=discovery_response(GUIDS[:1])
        ),
        patch("npdb.managers.midrc.httpx.stream", return_value=response(data)),
        patch.object(NeurobagelAnnotator, "execute", side_effect=annotate),
        patch.object(mappings, "CONFIG_NAMESPACES_MAPPING", namespaces),
        patch(
            "bagel.utilities.bids_utils.get_bids_suffix_to_std_term_mapping",
            return_value={"T2w": "nidm:T2Weighted"},
        ),
        patch(
            "bids_validator.BIDSValidator.is_bids",
            side_effect=AssertionError("Unexpected non-Rust dataset validation"),
        ),
    ):
        result = CliRunner().invoke(
            npdb,
            [
                "convert",
                "bagel",
                "midrc",
                URL,
                str(output),
                "--cache-dir",
                str(manager.cache_dir),
                "--neurobagel-modalities",
            ],
        )
    assert result.exit_code == 0, (result.output, result.exception)
    payload = json.loads((output / f"midrc_{DATASET}.jsonld").read_text())
    assert payload["hasSamples"][0]["hasLabel"] == "sub-01"
    serialized = json.dumps(payload)
    assert URL in serialized
    assert "T2Weighted" in serialized
    assert "42" in serialized


def test_midrc_structured_metadata_is_prepared_offline(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with zipfile.ZipFile(source / "Example_structured.zip", "w") as archive:
        archive.writestr(
            "case_export.tsv",
            "type\tsubmitter_id\tage_at_index\tsex\tage_at_index_gt89\ncase\tsite-001\t42\tFemale\tNo\n",
        )
    with zipfile.ZipFile(source / "Example_annotation.zip", "w") as archive:
        archive.writestr("image.nii.gz", b"image")
    provider = MIDRCProviderManager()
    with patch("npdb.managers.midrc.httpx.get") as network:
        extracted = provider.unpack(source, output_dir=tmp_path / "prepared")
        output = provider.prepare(extracted)
    network.assert_not_called()
    assert output == tmp_path / "prepared/Example"
    assert "sub-site001\t42\tF\tNo" in (output / "participants.tsv").read_text()
    assert (
        json.loads((output / "participants.json").read_text())["age"]["Units"] == "year"
    )
    assert (
        json.loads((output / "dataset_description.json").read_text())["Name"]
        == "Example"
    )
    assert (source / "Example_structured.zip").exists()
    assert (
        extracted / "sourcedata/midrc/annotation/image.nii.gz"
    ).read_bytes() == b"image"

    for archive in source.glob("*.zip"):
        (extracted / archive.name).write_bytes(archive.read_bytes())
    nested = extracted / "sourcedata/midrc/annotation/series.uid.zip"
    with zipfile.ZipFile(nested, "w") as archive:
        archive.writestr("instance.dcm", b"dicom")
    original_participants = (output / "participants.tsv").read_bytes()
    with (
        patch("npdb.managers.midrc_bids.extract_archive") as extraction,
        patch("npdb.managers.midrc_bids.prepare_metadata") as preparation,
        patch("npdb.managers.midrc_bids.convert_dicom") as conversion,
    ):
        assert provider.prepare_fetched(extracted) == output
    extraction.assert_not_called()
    preparation.assert_not_called()
    conversion.assert_not_called()
    assert (output / "participants.tsv").read_bytes() == original_participants


def test_midrc_subject_normalization_rejects_collisions():
    from npdb.managers.midrc_bids import subject_labels

    with pytest.raises(ValueError, match="collide"):
        subject_labels({"site-01": {}, "site01": {}})


def test_midrc_fetch_reuses_completed_preparation(manager, sdk, tmp_path):
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(
            "case.tsv",
            "type\tsubmitter_id\tage_at_index\tsex\ncase\tsite-01\t42\tFemale\n",
        )
    data = bundle.getvalue()
    setup_download(sdk, [record("Example_structured.zip", data)] * 2)
    root = tmp_path / "out"
    with patch(
        "npdb.managers.midrc.httpx.stream", return_value=response(data)
    ) as stream:
        output = manager.fetch(GUIDS[0], root)
        nested = root / "sourcedata/midrc/structured/series.uid.zip"
        with zipfile.ZipFile(nested, "w") as archive:
            archive.writestr("instance.dcm", b"dicom")
        with patch("npdb.managers.midrc_bids.prepare_metadata") as preparation:
            assert manager.fetch(GUIDS[0], root) == output
        preparation.assert_not_called()
    assert stream.call_count == 1
    assert next(manager.cache_dir.rglob("Example_structured.zip")).read_bytes() == data


@pytest.mark.parametrize(
    "missing",
    [
        None,
        "derivative_report.json",
        "dataset_description.json",
        "participants.tsv",
        "participants.json",
        "sub-01/anat/sub-01_T2w.nii.gz",
        "sub-01/anat/sub-01_T2w.json",
        "derivatives/segmentation/dataset_description.json",
        "derivatives/segmentation/sub-01/anat/sub-01_desc-SEG_dseg.nii.gz",
        "derivatives/segmentation/sub-01/anat/sub-01_desc-SEG_dseg.json",
    ],
)
def test_midrc_reuses_only_complete_reported_outputs(tmp_path, missing):
    staging = tmp_path / "sourcedata/midrc"
    staging.mkdir(parents=True)
    (staging / "archives.json").write_text(
        json.dumps({"Name": "Example", "Roles": ["structured"]})
    )
    output = tmp_path / "Example"
    raw = "sub-01/anat/sub-01_T2w.nii.gz"
    derivative = "derivatives/segmentation/sub-01/anat/sub-01_desc-SEG_dseg.nii.gz"
    required = [
        "dataset_description.json",
        "participants.tsv",
        "participants.json",
        raw,
        "sub-01/anat/sub-01_T2w.json",
        derivative,
        "derivatives/segmentation/sub-01/anat/sub-01_desc-SEG_dseg.json",
        "derivatives/segmentation/dataset_description.json",
    ]
    for relative in required:
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"preserved")
    report = staging / "derivative_report.json"
    report.write_text(
        json.dumps({"images": {"uid": [raw]}, "derivatives": {"mask": derivative}})
    )
    if missing:
        (report if missing == "derivative_report.json" else output / missing).unlink()
    with patch("npdb.managers.midrc_bids.prepare_metadata") as preparation:
        if missing is None:
            assert MIDRCProviderManager().prepare(tmp_path) == output
        else:
            with pytest.raises(
                (FileExistsError, FileNotFoundError), match="incomplete"
            ):
                MIDRCProviderManager().prepare(tmp_path)
    preparation.assert_not_called()
    assert (output / "participants.tsv").exists() == (missing != "participants.tsv")


def test_midrc_unpack_rejects_a_conflicting_existing_manifest(tmp_path):
    with zipfile.ZipFile(tmp_path / "Example_structured.zip", "w") as archive:
        archive.writestr("case.tsv", "metadata")
    staging = tmp_path / "sourcedata/midrc"
    staging.mkdir(parents=True)
    manifest = staging / "archives.json"
    manifest.write_text(json.dumps({"Name": "Other", "Roles": ["structured"]}))
    with (
        patch("npdb.managers.midrc_bids.extract_archive") as extraction,
        pytest.raises(ValueError, match="does not match"),
    ):
        MIDRCProviderManager().unpack(tmp_path)
    extraction.assert_not_called()
    assert json.loads(manifest.read_text())["Name"] == "Other"


def test_midrc_image_entities_use_uid_links_not_filename_numbers():
    from npdb.managers.midrc_bids import image_identities

    tables = {
        "case": [{"submitter_id": "site-001"}],
        "imaging_study": [
            {"submitter_id": "study.uid", "cases.submitter_id": "site-001"}
        ],
        "mr_series_file": [
            {
                "submitter_id": "series.uid",
                "series_uid": "series.uid",
                "imaging_studies.submitter_id": "study.uid",
                "case_ids": "site-001",
                "modality": "MR",
                "series_description": "sag t2",
                "echo_time": "99.36",
                "repetition_time": "2800",
            }
        ],
        "annotation_file": [
            {
                "file_name": "site-001_Study-MR-1_Series-22.nii.gz",
                "mr_series_files.submitter_id": "series.uid",
            }
        ],
    }
    identities, rejected = image_identities(tables)
    assert rejected == []
    assert identities["series.uid"]["stem"] == "sub-site001_ses-MR1_run-22"
    assert identities["series.uid"]["suffix"] == "T2w"
    assert identities["series.uid"]["metadata"]["EchoTime"] == pytest.approx(0.09936)
    assert identities["series.uid"]["metadata"]["RepetitionTime"] == pytest.approx(2.8)
    tables["annotation_file"][0]["imaging_studies.submitter_id"] = "wrong.study"
    assert "Annotation study link" in image_identities(tables)[1][0]["reason"]
    tables["annotation_file"][0].pop("imaging_studies.submitter_id")
    tables["mr_series_file"][0]["series_description"] = "unknown"
    identities, rejected = image_identities(tables)
    assert identities == {}
    assert "Unknown" in rejected[0]["reason"]


def test_midrc_converter_uses_safe_bids_arguments(tmp_path):
    from npdb.managers.midrc_bids import convert_dicom

    converted = tmp_path / "converted"

    def convert(command, **kwargs):
        assert command[command.index("-b") + 1] == "y"
        assert command[command.index("-ba") + 1] == "y"
        assert command[command.index("-z") + 1] == "y"
        assert command[-1] == str(tmp_path / "dicom")
        (converted / "image.nii.gz").write_bytes(b"nifti")
        return Mock(returncode=0)

    with (
        patch(
            "npdb.managers.midrc_bids.shutil.which", return_value="/usr/bin/dcm2niix"
        ),
        patch("npdb.managers.midrc_bids.subprocess.run", side_effect=convert),
    ):
        assert convert_dicom(tmp_path / "dicom", converted) == [
            converted / "image.nii.gz"
        ]


def test_midrc_derivative_preserves_payload_and_uses_raw_entities(tmp_path):
    import nibabel as nib
    import numpy as np

    from npdb.managers.midrc_bids import prepare_derivatives

    staging = tmp_path / "source"
    masks = staging / "segmentation"
    masks.mkdir(parents=True)
    (staging / "archives.json").write_text(
        json.dumps(
            {
                "Name": "Example",
                "Roles": ["structured", "imaging_files", "segmentation"],
            }
        )
    )
    source = masks / "site-001_Study-MR-1_Series-22_SEG.nii.gz"
    nib.save(nib.Nifti1Image(np.ones((2, 2, 2), dtype=np.int16), np.eye(4)), source)
    original = source.read_bytes()
    output = tmp_path / "bids"
    raw_path = "sub-site001/ses-MR1/anat/sub-site001_ses-MR1_run-22_T2w.nii.gz"
    raw = output / raw_path
    raw.parent.mkdir(parents=True)
    raw.with_name(raw.name.removesuffix(".nii.gz") + ".json").write_text(
        '{"EchoTime": 0.1, "SliceTiming": [0, 0.1]}'
    )
    tables = {
        "annotation_file": [
            {
                "file_name": source.name,
                "mr_series_files.submitter_id": "uid",
                "annotation_name": "labels",
                "annotation_method": "manual",
            }
        ]
    }
    report = {"images": {"uid": [raw_path]}, "quarantine": []}
    prepare_derivatives(staging, output, tables, report)
    destination = (
        output
        / "derivatives/segmentation/sub-site001/ses-MR1/anat/sub-site001_ses-MR1_run-22_desc-SEG_dseg.nii.gz"
    )
    assert destination.read_bytes() == original
    assert source.read_bytes() == original
    metadata = json.loads(
        destination.with_name(
            destination.name.removesuffix(".nii.gz") + ".json"
        ).read_text()
    )
    assert metadata["Sources"] == ["bids:raw:" + raw_path]
    assert metadata["OriginalFileName"] == source.name
    assert metadata["AnnotationMethod"] == "manual"
    assert "SliceTiming" not in metadata
    assert report["quarantine"] == []


def test_midrc_annotation_skull_stripping_is_established_from_payload(tmp_path):
    import nibabel as nib
    import numpy as np

    from npdb.managers.midrc_bids import prepare_derivatives

    staging = tmp_path / "source"
    annotations = staging / "annotation"
    annotations.mkdir(parents=True)
    (staging / "archives.json").write_text(
        json.dumps({"Name": "Example", "Roles": ["annotation"]})
    )
    source = annotations / "original.nii.gz"
    image = nib.Nifti1Image(np.ones((2, 2, 2), dtype=np.int16), np.eye(4))
    nib.save(image, source)
    output = tmp_path / "bids"
    raw_path = "sub-01/ses-1/anat/sub-01_ses-1_run-1_T2w.nii.gz"
    raw = output / raw_path
    raw.parent.mkdir(parents=True)
    nib.save(image, raw)
    raw.with_name(raw.name.removesuffix(".nii.gz") + ".json").write_text("{}")
    tables = {
        "annotation_file": [
            {"file_name": source.name, "mr_series_files.submitter_id": "uid"}
        ]
    }
    report = {"images": {"uid": [raw_path]}, "quarantine": []}
    prepare_derivatives(staging, output, tables, report)
    sidecar = output / "derivatives/annotation" / Path(raw_path).with_suffix("")
    metadata = json.loads(sidecar.with_suffix(".json").read_text())
    assert metadata["SkullStripped"] is False
    assert report["quarantine"] == []

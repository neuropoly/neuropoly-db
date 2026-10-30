"""Adequacy suite for external databases (providers).

Every provider must, through the real CLI:
- produce ingestable Neurobagel JSON-LD from a delivered BIDS dataset,
- explain each anticipated failure (missing extra, credentials, cache, HTTP 401/403/404,
  network, unusable layout) without a traceback or a bug report.

Only the network boundary (``Provider.fetch`` download step) is faked; unpacking,
preparation, BIDS validation, Neurobagel conversion and error reporting are real.
Add a ``PROVIDERS`` entry when adding a provider.
"""

import gzip
import io
import json
import shutil
import sys
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass
from inspect import signature
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from npdb.cli.cli import npdb
from npdb.providers.figshare import FigshareProvider
from npdb.providers.git import GitProvider
from npdb.providers.kaggle import KaggleProvider
from npdb.providers.mendeley import MendeleyProvider
from npdb.providers.midrc import MIDRCProvider
from npdb.providers.openneuro import OpenNeuroProvider
from npdb.providers.zenodo import ZenodoProvider

runner = CliRunner()


@dataclass
class Provider:
    command: str
    provider: type
    identifier: str
    extra_module: str | None = None
    options: tuple[str, ...] = ()
    config_error: tuple[tuple[str, ...], str] | None = (
        None  # (options, expected fragment)
    )


PROVIDERS = [
    Provider("git", GitProvider, "https://github.com/example/ds.git"),
    Provider(
        "kaggle",
        KaggleProvider,
        "user/ds",
        "kagglehub",
        ("--cache-dir", "{tmp}/cache"),
        config_error=((), "--cache-dir"),
    ),
    Provider("mendeley", MendeleyProvider, "yd695ynbdz"),
    Provider(
        "midrc",
        MIDRCProvider,
        "dg.MD1R/ee75bf02-33e9-4d89-ba0a-868dd4aec81c",
        "gen3.auth",
        ("--credentials-path", "{tmp}/credentials.json"),
        config_error=((), "credentials"),
    ),
    Provider("openneuro", OpenNeuroProvider, "ds000001", "openneuro"),
    Provider("zenodo", ZenodoProvider, "123456", "zenodo_get"),
    Provider("figshare", FigshareProvider, "123456"),
]
IDS = [p.command for p in PROVIDERS]


def args(provider, tmp_path, options=None):
    opts = provider.options if options is None else options
    (tmp_path / "credentials.json").write_text("{}")
    return [
        "convert",
        "bagel",
        provider.command,
        provider.identifier,
        str(tmp_path / "out"),
        "--neurobagel-modalities",
        *(o.format(tmp=tmp_path) for o in opts),
    ]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    for name in (
        "NP_NPDB_CACHE_DIR",
        "NP_MIDRC_CREDENTIALS",
        "NP_MENDELEY_ACCESS_TOKEN",
        "NP_ZENODO_TOKEN",
        "NP_FIGSHARE_TOKEN",
        "NP_GIT_USER",
        "NP_GIT_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_interactive_annotation(request, monkeypatch):
    # Manual annotation waits for a human; outside the ingest test, reaching it is a failure.
    if request.node.originalname in {
        "test_delivered_dataset_is_ingestable",
        "test_neurodata_dataset_is_ingestable",
    }:
        return
    from npdb.annotators import NeurobagelAnnotator

    async def unexpected(*_, **__):
        raise AssertionError("conversion reached interactive annotation")

    monkeypatch.setattr(NeurobagelAnnotator, "execute", unexpected)


# Archive layouts seen in the wild; every provider must find the BIDS root in each.
LAYOUTS = {
    "flat": "",
    "release-wrapper": "lab-dataset-2b5f8f7/",
    "macos-wrapper": "dataset/",
    "rawdata": "rawdata/",
}


def bids_archive(layout: str = "flat") -> bytes:
    import nibabel as nib
    import numpy as np

    prefix = LAYOUTS[layout]
    image = nib.Nifti1Image(np.zeros((2, 2, 2), dtype=np.int16), np.eye(4))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            f"{prefix}sub-01/anat/sub-01_T2w.nii.gz", gzip.compress(image.to_bytes())
        )
        archive.writestr(
            f"{prefix}participants.tsv", "participant_id\tage\tsex\nsub-01\t42\tF\n"
        )
        description = json.dumps({"Name": "Adequacy", "BIDSVersion": "1.9.0"})
        # BIDS keeps dataset_description.json at the top level next to rawdata/.
        archive.writestr(
            (
                "dataset_description.json"
                if layout == "rawdata"
                else f"{prefix}dataset_description.json"
            ),
            description,
        )
        if layout == "macos-wrapper":
            archive.writestr(
                "__MACOSX/dataset/sub-01/._sub-01_T2w.nii.gz", b"resource fork"
            )
    return stream.getvalue()


def deliver(payload: dict[str, bytes]):
    """Fake download: write files into the fetch directory, then run the real preparation."""

    def fetch(self, identifier, output_dir, **_):
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        for name, content in payload.items():
            (output / name).write_bytes(content)
        return self.prepare_fetched(output)

    return fetch


def fail_with(exc):
    def fetch(self, identifier, output_dir, **_):
        raise exc

    return fetch


def http_error(status):
    request = httpx.Request("GET", "https://provider.example/api")
    return httpx.HTTPStatusError(
        f"HTTP {status}",
        request=request,
        response=httpx.Response(status, request=request),
    )


def ingest_patches(stack: ExitStack):
    """Deterministic annotation and offline Neurobagel vocabularies; conversion itself is real."""
    from bagel import mappings
    from bagel.cli import pheno

    from npdb.annotators import NeurobagelAnnotator

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
    choices = signature(pheno).parameters["config"].default.click_type
    stack.enter_context(
        patch.object(NeurobagelAnnotator, "execute", side_effect=annotate)
    )
    stack.enter_context(patch.object(mappings, "CONFIG_NAMESPACES_MAPPING", namespaces))
    stack.enter_context(patch.object(mappings, "CONFIG_NAMESPACES_FETCHING_ERR", None))
    stack.enter_context(patch.object(choices, "choices", ("Neurobagel",)))
    stack.enter_context(
        patch(
            "bagel.utilities.bids_utils.get_bids_suffix_to_std_term_mapping",
            return_value={"T2w": "nidm:T2Weighted"},
        )
    )


def has_validator():
    from bids_repair.native import validator_status

    return not validator_status().startswith("unavailable")


def assert_ingestable(result, output: Path):
    assert result.exit_code == 0, result.output
    documents = list(output.glob("*.jsonld"))
    assert len(documents) == 1, result.output
    payload = json.loads(documents[0].read_text())
    assert payload["hasSamples"][0]["hasLabel"] == "sub-01"
    assert "T2Weighted" in json.dumps(payload)


def assert_explained(result, *fragments):
    assert result.exit_code == 1, result.output
    assert "Traceback (most recent call last)" not in result.output
    assert "bug in npdb" not in result.output
    for fragment in fragments:
        assert fragment in result.output, result.output


@pytest.mark.skipif(not has_validator(), reason="pinned Rust validator unavailable")
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("provider", PROVIDERS, ids=IDS)
def test_delivered_dataset_is_ingestable(provider, layout, tmp_path):
    with ExitStack() as stack:
        ingest_patches(stack)
        stack.enter_context(
            patch.object(
                provider.provider,
                "fetch",
                deliver({"dataset.zip": bids_archive(layout)}),
            )
        )
        result = runner.invoke(npdb, args(provider, tmp_path))
    assert_ingestable(result, tmp_path / "out")


@pytest.mark.skipif(not has_validator(), reason="pinned Rust validator unavailable")
def test_neurodata_dataset_is_ingestable(tmp_path):
    def clone_repository(dataset, local_path, light=False):
        with zipfile.ZipFile(io.BytesIO(bids_archive())) as archive:
            archive.extractall(local_path)

    provider = type(
        "NeuroData",
        (),
        {
            "clone_repository": staticmethod(clone_repository),
            "get_description_extensions": staticmethod(
                lambda d: (f"https://data.neuro.polymtl.ca/datasets/{d}", "restricted")
            ),
        },
    )()
    with ExitStack() as stack:
        ingest_patches(stack)
        stack.enter_context(patch("npdb.cli.cli._load_env"))
        stack.enter_context(
            patch(
                "npdb.cli.cli.GiteaProviderFactory.create_from_env", return_value=provider
            )
        )
        result = runner.invoke(
            npdb,
            [
                "convert",
                "bagel",
                "gitea",
                "ds",
                str(tmp_path / "out"),
                "--neurobagel-modalities",
            ],
        )
    assert_ingestable(result, tmp_path / "out")


@pytest.mark.parametrize(
    "provider", [p for p in PROVIDERS if p.extra_module], ids=lambda p: p.command
)
def test_missing_extra_is_explained(provider, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, provider.extra_module, None)
    result = runner.invoke(npdb, args(provider, tmp_path))
    extra = {"gen3.auth": "midrc", "kagglehub": "kaggle", "zenodo_get": "zenodo"}.get(
        provider.extra_module, provider.extra_module
    )
    assert_explained(result, "Missing dependency", f"npdb[{extra}]")


@pytest.mark.parametrize(
    "provider", [p for p in PROVIDERS if p.config_error], ids=lambda p: p.command
)
def test_missing_configuration_is_explained(provider, tmp_path, monkeypatch):
    if provider.extra_module:
        monkeypatch.setitem(sys.modules, provider.extra_module, type(sys)("stub"))
        for name in ("Gen3Auth", "Gen3File", "Gen3Index", "dataset_download"):
            setattr(sys.modules[provider.extra_module], name, object)
        if provider.extra_module == "gen3.auth":
            for module in ("gen3.file", "gen3.index"):
                monkeypatch.setitem(sys.modules, module, sys.modules["gen3.auth"])
    options, fragment = provider.config_error
    result = runner.invoke(npdb, args(provider, tmp_path, options))
    assert_explained(result, "Configuration error", fragment, f"Development-Provider-")


REMOTE_FAILURES = [
    ("unauthorized", http_error(401), ["HTTP 401", "credentials"]),
    ("forbidden", http_error(403), ["HTTP 403", "credentials"]),
    ("not-found", http_error(404), ["HTTP 404", "identifier"]),
    ("offline", httpx.ConnectError("connection refused"), ["Network error", "VPN"]),
]


@pytest.mark.parametrize("provider", PROVIDERS, ids=IDS)
@pytest.mark.parametrize(
    ("case", "exc", "fragments"), REMOTE_FAILURES, ids=[c[0] for c in REMOTE_FAILURES]
)
def test_remote_failures_are_explained(provider, case, exc, fragments, tmp_path):
    with patch.object(provider.provider, "fetch", fail_with(exc)):
        result = runner.invoke(npdb, args(provider, tmp_path))
    assert_explained(result, *fragments, "Development-Provider-")


@pytest.mark.parametrize("provider", PROVIDERS, ids=IDS)
def test_unusable_layout_is_explained(provider, tmp_path):
    payload = {"README.md": b"not a BIDS dataset"}
    with patch.object(provider.provider, "fetch", deliver(payload)):
        result = runner.invoke(npdb, args(provider, tmp_path))
    assert_explained(result)


@pytest.mark.xfail(
    strict=True,
    reason="npdb download only resolves NeuroData/HTTP rows; align it with ingestion providers",
)
@pytest.mark.parametrize("provider", PROVIDERS, ids=IDS)
def test_download_uses_the_ingestion_provider(provider, tmp_path):
    tsv = tmp_path / "q.tsv"
    tsv.write_text(
        "DatasetName\tSubjectID\tRepositoryURL\tImagingSessionPath\tAccessLink\n"
        f"ds\tsub-01\t{provider.command}:{provider.identifier}\tsub-01\t\n"
    )
    from npdb.errors import ConfigurationError

    with (
        patch("npdb.cli.cli._load_env"),
        patch(
            "npdb.cli.cli.GiteaProviderFactory.create_from_env",
            side_effect=ConfigurationError("offline"),
        ),
        patch("npdb.factories.ProviderFactory.create") as create,
    ):
        runner.invoke(npdb, ["download", str(tsv), "-o", str(tmp_path / "dl")])
    create.assert_called_once()

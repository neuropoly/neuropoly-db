"""Tests for automatic provider selection in `npdb download` (#66)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from npdb.cli import cli
from npdb.providers.detection import (
    DatasetMatch,
    bids_relative_path,
    detect_provider,
    route_for,
)
from npdb.providers.model import ProviderName

OPENNEURO = ProviderName.OPENNEURO
JSONLD_REPO = "https://github.com/OpenNeuroDatasets-JSONLD/ds006036.git"
HEADER = "DatasetName\tRepositoryURL\tSubjectID\tImagingSessionPath\tAccessLink\n"


# --------------------------------------------------------------------------- helpers


def write_tsv(path: Path, rows: list[tuple[str, str, str, str, str]]) -> Path:
    path.write_text(HEADER + "".join("\t".join(row) + "\n" for row in rows))
    return path


def all_output(result) -> str:
    """stdout + stderr, whatever the installed Click version does with stderr."""
    text = result.output
    try:
        text += result.stderr
    except ValueError:  # older Click: stderr already mixed into output
        pass
    return text


class FakeProvider:
    """Records fetch() calls instead of downloading anything."""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, Path, dict]] = []
        self.fail = fail

    def fetch(self, identifier, output_dir, **kwargs):
        self.calls.append((identifier, Path(output_dir), kwargs))
        if self.fail:
            raise RuntimeError("simulated provider failure")
        return Path(output_dir)


@pytest.fixture
def fake_provider(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(cli.ProviderFactory, "create", lambda *a, **k: provider)
    # OpenNeuro must never need NeuroData credentials.
    for var in ("NP_GITEA_APP_URL", "NP_GITEA_APP_USER", "NP_GITEA_APP_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    return provider


def run_download(tsv: Path, out: Path):
    return CliRunner().invoke(cli.npdb, ["download", str(tsv), "-o", str(out)])


# ------------------------------------------------------------------ detect_provider


@pytest.mark.parametrize(
    "row, expected",
    [
        ({"RepositoryURL": JSONLD_REPO}, DatasetMatch(OPENNEURO, "ds006036")),
        (
            {"RepositoryURL": "https://github.com/OpenNeuroDatasets/ds006036"},
            DatasetMatch(OPENNEURO, "ds006036"),
        ),
        (
            {
                "RepositoryURL": "",
                "AccessLink": "https://openneuro.org/datasets/ds000102",
            },
            DatasetMatch(OPENNEURO, "ds000102"),
        ),
        (
            {"AccessLink": "https://openneuro.org/datasets/ds000001/versions/1.1.2"},
            DatasetMatch(OPENNEURO, "ds000001", "1.1.2"),
        ),
        (
            {
                "RepositoryURL": "https://data.anc.plus.ac.at/",
                "AccessLink": "https://bids-datasets.data-pages.anc.plus.ac.at/auditory/x/",
            },
            None,
        ),
        ({"RepositoryURL": "", "AccessLink": ""}, None),
        ({}, None),
    ],
)
def test_detect_provider(row, expected):
    assert detect_provider(row) == expected


def test_repository_url_has_priority_over_access_link():
    row = {
        "RepositoryURL": JSONLD_REPO,
        "AccessLink": "https://openneuro.org/datasets/ds000999",
    }
    assert detect_provider(row).identifier == "ds006036"


# --------------------------------------------------------------- bids_relative_path


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/ds006036/sub-001", "sub-001"),
        ("/ds006036/sub-01/ses-01", "sub-01/ses-01"),
        ("/some/other/root/sub-02", "sub-02"),
        ("sub-002", "sub-002"),
        ("", None),
        ("protected", None),
    ],
)
def test_bids_relative_path(path, expected):
    assert bids_relative_path(path) == expected


# ------------------------------------------------------------------- fetch_kwargs


def test_openneuro_fetch_kwargs_request_real_data_for_listed_paths():
    kwargs = route_for(OPENNEURO).fetch_kwargs(["sub-01", "sub-02"], "1.0.0")
    assert kwargs == {
        "metadata_only": False,
        "include": ["sub-01", "sub-02"],
        "tag": "1.0.0",
    }


# ---------------------------------------------------------------- npdb download


def test_download_routes_openneuro_rows_to_provider(tmp_path, fake_provider):
    tsv = write_tsv(
        tmp_path / "results.tsv",
        [
            ("EEG", JSONLD_REPO, "sub-001", "/ds006036/sub-001", ""),
            ("EEG", JSONLD_REPO, "sub-001", "", ""),  # phenotypic row, no imaging
            ("EEG", JSONLD_REPO, "sub-002", "/ds006036/sub-002", ""),
        ],
    )
    out = (tmp_path / "out").resolve()

    result = run_download(tsv, out)

    assert result.exit_code == 0, all_output(result)
    assert fake_provider.calls == [
        (
            "ds006036",
            out / "ds006036",
            {"metadata_only": False, "include": ["sub-001", "sub-002"], "tag": None},
        )
    ]


def test_download_passes_url_version_as_tag(tmp_path, fake_provider):
    page = "https://openneuro.org/datasets/ds000001/versions/1.1.2"
    tsv = write_tsv(
        tmp_path / "results.tsv",
        [("Data", page, "sub-01", "/ds000001/sub-01", page)],
    )

    result = run_download(tsv, (tmp_path / "out").resolve())

    assert result.exit_code == 0, all_output(result)
    assert fake_provider.calls[0][2]["tag"] == "1.1.2"


def test_download_reports_openneuro_page_without_subjects(
    tmp_path, fake_provider, monkeypatch
):
    """#72: restricted dataset, landing page link, 'protected' subject fields."""
    page = "https://openneuro.org/datasets/ds000001/versions/1.1.2"
    tsv = write_tsv(
        tmp_path / "results.tsv",
        [("whole-spine", page, "protected", "protected", page)],
    )
    monkeypatch.setattr(
        cli, "fetch_url", lambda *a, **k: pytest.fail("HTTP backend must not run")
    )
    out = (tmp_path / "out").resolve()

    result = run_download(tsv, out)

    assert result.exit_code == 1
    assert "no imaging paths" in all_output(result)
    assert fake_provider.calls == []
    assert not (out / "whole-spine").exists()


def test_download_counts_provider_failures(tmp_path, monkeypatch):
    failing = FakeProvider(fail=True)
    monkeypatch.setattr(cli.ProviderFactory, "create", lambda *a, **k: failing)
    tsv = write_tsv(
        tmp_path / "results.tsv",
        [("EEG", JSONLD_REPO, "sub-001", "/ds006036/sub-001", "")],
    )

    result = run_download(tsv, (tmp_path / "out").resolve())

    assert result.exit_code == 1
    output = all_output(result)
    assert "FAIL openneuro:ds006036" in output
    assert "providers: 1" in output


def test_download_mixes_provider_and_http_datasets(
    tmp_path, fake_provider, monkeypatch
):
    http_calls = []

    def fake_fetch_url(url, dest, *args, **kwargs):
        http_calls.append(url)
        return True, f"Downloaded: {Path(dest).name}"

    monkeypatch.setattr(cli, "fetch_url", fake_fetch_url)
    zip_link = "https://data.example.org/files/sub-01.zip"
    tsv = write_tsv(
        tmp_path / "results.tsv",
        [
            ("EEG", JSONLD_REPO, "sub-001", "/ds006036/sub-001", ""),
            ("Other", "https://data.example.org/", "sub-01", "/other/sub-01", zip_link),
        ],
    )

    result = run_download(tsv, (tmp_path / "out").resolve())

    assert result.exit_code == 0, all_output(result)
    assert [call[0] for call in fake_provider.calls] == ["ds006036"]
    assert http_calls == [zip_link]

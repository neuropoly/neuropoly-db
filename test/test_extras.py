"""Optional extras: limited installs must load and fail with actionable messages."""

import asyncio
import sys
import tomllib
from pathlib import Path

import pytest

from npdb.extras import (
    MissingExecutableError,
    MissingExtraError,
    require_executable,
    require_module,
)
from npdb.managers.model import GitManager, ProviderName

ROOT = Path(__file__).resolve().parents[1]


def test_require_module_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "npdb_missing_dep", None)
    with pytest.raises(MissingExtraError) as err:
        require_module("npdb_missing_dep", extra="kaggle", feature="Thing")
    assert err.value.extra == "kaggle"
    assert "pip install 'npdb[kaggle]'" in err.value.hint


def test_require_executable(monkeypatch):
    monkeypatch.setattr("npdb.extras.shutil.which", lambda _: None)
    with pytest.raises(MissingExecutableError, match="'git-annex' on PATH"):
        require_executable("git-annex", feature="Annex")


@pytest.mark.parametrize(
    ("module", "extra", "factory"),
    [
        ("kagglehub", "kaggle", lambda tmp: _kaggle(tmp)),
        ("openneuro", "openneuro", lambda tmp: _openneuro()),
        ("zenodo_get", "zenodo", lambda tmp: _zenodo()),
        ("gen3.auth", "midrc", lambda tmp: _midrc()),
    ],
)
def test_provider_fetch_reports_missing_extra(monkeypatch, tmp_path, module, extra, factory):
    monkeypatch.setitem(sys.modules, module, None)
    manager = factory(tmp_path)
    with pytest.raises(MissingExtraError) as err:
        manager.fetch("identifier", tmp_path / "out")
    assert err.value.extra == extra
    assert not (tmp_path / "out").exists()


def _kaggle(tmp):
    from npdb.managers.kaggle import KaggleProviderManager

    return KaggleProviderManager(cache_dir=tmp / "cache")


def _openneuro():
    from npdb.managers.openneuro import OpenNeuroProviderManager

    return OpenNeuroProviderManager()


def _zenodo():
    from npdb.managers.zenodo import ZenodoProviderManager

    return ZenodoProviderManager()


def _midrc():
    from npdb.managers.midrc import MIDRCProviderManager

    return MIDRCProviderManager(credentials_path=None)


def test_gitea_manager_reports_missing_extra(monkeypatch):
    monkeypatch.setattr("npdb.external.neurogitea.gitea.gt_client", None)
    from npdb.factories import GiteaManagerFactory

    monkeypatch.setenv("NP_GITEA_APP_URL", "https://example.invalid")
    monkeypatch.setenv("NP_GITEA_APP_USER", "user")
    monkeypatch.setenv("NP_GITEA_APP_TOKEN", "token")
    with pytest.raises(MissingExtraError) as err:
        GiteaManagerFactory.create_from_env()
    assert err.value.extra == "gitea"


def test_browser_automation_reports_missing_extra(monkeypatch):
    monkeypatch.setattr("npdb.external.neurobagel.automation.async_playwright", None)
    from npdb.external.neurobagel.automation import NBAnnotationToolBrowserSession

    with pytest.raises(MissingExtraError) as err:
        asyncio.run(NBAnnotationToolBrowserSession().launch())
    assert err.value.extra == "annotation-automation"


@pytest.mark.parametrize(
    ("cmd", "missing"),
    [(["git", "status"], "git"), (["git", "-C", ".", "annex", "get"], "git-annex")],
)
def test_run_git_requires_executables_without_retry(monkeypatch, cmd, missing):
    calls = []

    def which(name):
        calls.append(name)
        return None if name == missing else f"/usr/bin/{name}"

    monkeypatch.setattr("npdb.extras.shutil.which", which)
    with pytest.raises(MissingExecutableError, match=f"'{missing}' on PATH"):
        GitManager("user", "token")._run_git(cmd, env={}, context="test")
    assert calls.count(missing) == 1


def test_cli_prints_missing_extra_without_traceback(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from npdb.cli.cli import npdb

    monkeypatch.setitem(sys.modules, "kagglehub", None)
    result = CliRunner().invoke(
        npdb,
        ["convert", "bagel", "kaggle", "u/d", str(tmp_path / "out"),
         "--cache-dir", str(tmp_path / "cache")],
    )
    assert result.exit_code == 1
    assert "requires the 'kaggle' extra" in result.output
    assert "pip install 'npdb[kaggle]'" in result.output
    assert "Traceback" not in result.output


def test_all_extra_covers_every_provider():
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "optional-dependencies"
    ]
    providers = {name.value for name in ProviderName} | {"gitea"}
    assert providers <= extras.keys()
    all_extras = extras["all"][0].split("[", 1)[1].rstrip("]").split(",")
    assert set(all_extras) == providers
    assert extras["download"] == ["npdb[gitea]"]

"""
Tests for Factory Method Pattern implementations in npdb.factories.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from npdb.annotation import AnnotationConfig
from npdb.factories import (
    AIClientFactory,
    AnnotationConfigFactory,
    GitManagerFactory,
    LedgerFactory,
)
from npdb.report import RunLedger

# ── GitManagerFactory ────────────────────────────────────────────


class TestGitManagerFactory:
    def test_github_does_not_require_gitea_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("npdb.factories.GithubManager") as MockManager:
                manager = GitManagerFactory.create_from_env(
                    "https://github.com/OpenNeuroDatasets-JSONLD/ds004011.git",
                    ssl_verify=False,
                )

        assert manager is not None
        MockManager.assert_called_once_with(ssl_verify=False)

    def test_neurodata_requires_all_environment_variables(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError) as exc_info:
                GitManagerFactory.create_from_env(
                    "https://data.neuro.polymtl.ca/datasets/whole-spine"
                )

        message = str(exc_info.value)
        assert "NP_GITEA_APP_URL" in message
        assert "NP_GITEA_APP_USER" in message
        assert "NP_GITEA_APP_TOKEN" in message

    def test_creates_neurodata_manager_with_all_variables(self):
        env = {
            "NP_GITEA_APP_URL": "https://data.neuro.polymtl.ca",
            "NP_GITEA_APP_USER": "testuser",
            "NP_GITEA_APP_TOKEN": "tok_abc123",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("npdb.factories.DataNeuroPolyMTL") as MockManager,
        ):
            manager = GitManagerFactory.create_from_env(
                "https://data.neuro.polymtl.ca/datasets/whole-spine",
                ssl_verify=False,
            )

        assert manager is not None
        MockManager.assert_called_once_with(
            url="https://data.neuro.polymtl.ca",
            user="testuser",
            token="tok_abc123",
            ssl_verify=False,
        )

    def test_rejects_unsupported_host(self):
        with pytest.raises(ValueError, match="Unsupported git host"):
            GitManagerFactory.create_from_env(
                "https://gitlab.example.com/group/repository.git"
            )


# ── AnnotationConfigFactory ────────────────────────────────────────


class TestAnnotationConfigFactory:
    def test_minimal_config(self):
        config = AnnotationConfigFactory.create_from_cli_args(mode="manual")
        assert isinstance(config, AnnotationConfig)
        assert config.mode == "manual"
        assert config.headless is True
        assert config.timeout == 300
        assert config.dry_run is False

    def test_all_args_forwarded(self):
        p = Path("/tmp/phenotype.json")
        h = Path("/tmp/header.json")
        a = Path("/tmp/artifacts")
        config = AnnotationConfigFactory.create_from_cli_args(
            mode="auto",
            headless=False,
            timeout=60,
            artifacts_dir=a,
            ai_provider="ollama",
            ai_model="neural-chat",
            phenotype_dictionary=p,
            header_map=h,
            dry_run=True,
            keep_annotations=True,
            no_new_columns=True,
        )
        assert config.mode == "auto"
        assert config.headless is False
        assert config.timeout == 60
        assert config.artifacts_dir == a
        assert config.ai_provider == "ollama"
        assert config.ai_model == "neural-chat"
        assert config.phenotype_dictionary == p
        assert config.header_map == h
        assert config.dry_run is True
        assert config.keep_annotations is True
        assert config.no_new_columns is True

    def test_optional_fields_default_to_none(self):
        config = AnnotationConfigFactory.create_from_cli_args(mode="assist")
        assert config.artifacts_dir is None
        assert config.ai_provider is None
        assert config.ai_model is None
        assert config.phenotype_dictionary is None
        assert config.header_map is None


# ── AIClientFactory ────────────────────────────────────────────────


class TestAIClientFactory:
    def test_unsupported_provider_raises(self):
        with pytest.raises(ValueError, match="Unsupported AI provider"):
            AIClientFactory.create("noexist", "model")

    def test_ollama_import_error(self):
        """If ollama is not installed, a clear ImportError is raised."""
        import sys

        with patch.dict(sys.modules, {"ollama": None}):
            with pytest.raises((ImportError, ValueError)):
                AIClientFactory.create("ollama", "neural-chat")

    def test_ollama_creates_client(self):
        mock_ollama = MagicMock()
        mock_ollama.Client.return_value = MagicMock()
        import sys

        with patch.dict(sys.modules, {"ollama": mock_ollama}):
            client = AIClientFactory.create("ollama", "neural-chat")
        mock_ollama.Client.assert_called_once()
        assert client is not None

    def test_azure_openai_missing_vars(self):
        """AzureOpenAI requires AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT."""
        mock_openai = MagicMock()
        import sys

        with patch.dict(sys.modules, {"openai": mock_openai}):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AZURE_OPENAI_API_KEY", None)
                os.environ.pop("AZURE_OPENAI_ENDPOINT", None)
                with pytest.raises(ValueError, match="Azure OpenAI"):
                    AIClientFactory.create("azure_openai", "gpt-4")


# ── LedgerFactory ─────────────────────────────────────────────────


class TestLedgerFactory:
    def test_creates_run_ledger(self):
        ledger = LedgerFactory.create()
        assert isinstance(ledger, RunLedger)
        assert ledger.path is None
        assert ledger.outcome == "pending"

    def test_creates_run_ledger_with_path(self, tmp_path):
        p = tmp_path / "run.json"
        ledger = LedgerFactory.create(path=p)
        assert ledger.path == p

    def test_created_ledger_is_mutable(self):
        ledger = LedgerFactory.create()
        ledger.record_failure("something went wrong")
        assert ledger.outcome == "failure"
        assert "something went wrong" in ledger.errors

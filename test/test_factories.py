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
    GiteaManagerFactory,
    LedgerFactory,
)
from npdb.report import RunLedger

# ── GiteaManagerFactory ────────────────────────────────────────────


class TestGiteaManagerFactory:
    def test_raises_when_all_vars_missing(self):
        env = {
            "NP_GITEA_APP_URL": None,
            "NP_GITEA_APP_USER": None,
            "NP_GITEA_APP_TOKEN": None,
        }
        with patch.dict(os.environ, {}, clear=True):
            for k in ("NP_GITEA_APP_URL", "NP_GITEA_APP_USER", "NP_GITEA_APP_TOKEN"):
                os.environ.pop(k, None)
            with pytest.raises(ValueError) as exc_info:
                GiteaManagerFactory.create_from_env()
        msg = str(exc_info.value)
        assert "NP_GITEA_APP_URL" in msg
        assert "NP_GITEA_APP_USER" in msg
        assert "NP_GITEA_APP_TOKEN" in msg

    def test_raises_when_one_var_missing(self):
        env = {
            "NP_GITEA_APP_URL": "https://example.com",
            "NP_GITEA_APP_USER": "user",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("NP_GITEA_APP_TOKEN", None)
            with pytest.raises(ValueError) as exc_info:
                GiteaManagerFactory.create_from_env()
        assert "NP_GITEA_APP_TOKEN" in str(exc_info.value)

    def test_creates_manager_with_all_vars(self):
        env = {
            "NP_GITEA_APP_URL": "https://gitea.example.com",
            "NP_GITEA_APP_USER": "testuser",
            "NP_GITEA_APP_TOKEN": "tok_abc123",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch("npdb.factories.DataNeuroPolyMTL") as MockMgr,
        ):
            MockMgr.return_value = object()
            manager = GiteaManagerFactory.create_from_env(ssl_verify=False)
        assert manager is not None
        MockMgr.assert_called_once_with(
            url="https://gitea.example.com",
            user="testuser",
            token="tok_abc123",
            ssl_verify=False,
        )

    def test_error_message_lists_all_missing_vars(self):
        """ValueError message must list every missing variable, not just the first."""
        with patch.dict(os.environ, {}, clear=True):
            for k in ("NP_GITEA_APP_URL", "NP_GITEA_APP_USER", "NP_GITEA_APP_TOKEN"):
                os.environ.pop(k, None)
            with pytest.raises(ValueError) as exc_info:
                GiteaManagerFactory.create_from_env()
        msg = str(exc_info.value)
        # All three must appear in a single error
        assert msg.count("NP_GITEA") == 3


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

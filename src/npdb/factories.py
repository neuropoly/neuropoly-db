"""
Factory Method Pattern — centralises all os.environ access and object
construction so that the rest of the codebase never reads env-vars directly.

Each factory provides a single ``create_*`` class method that either succeeds
and returns a fully-configured object, or raises a clear ``ValueError`` that
names every missing environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from npdb.annotation import AnnotationConfig
from npdb.annotation.modes import AnnotationMode
from npdb.managers.figshare import FigshareProviderManager
from npdb.managers.git import GitProviderManager
from npdb.managers.kaggle import KaggleProviderManager
from npdb.managers.mendeley import MendeleyProviderManager
from npdb.managers.midrc import MIDRCProviderManager
from npdb.managers.model import ProviderName
from npdb.managers.neuropoly import DataNeuroPolyMTL
from npdb.managers.openneuro import OpenNeuroProviderManager
from npdb.managers.zenodo import ZenodoProviderManager
from npdb.report import RunLedger


class GiteaManagerFactory:
    """
    Creates :class:`~npdb.managers.DataNeuroPolyMTL` from environment variables.

    Required variables:
    - ``NP_GITEA_APP_URL``   — base URL of the Gitea instance
    - ``NP_GITEA_APP_USER``  — Gitea username
    - ``NP_GITEA_APP_TOKEN`` — Gitea personal-access token

    Raises:
        ValueError: when any required variable is absent.
    """

    _URL_VAR = "NP_GITEA_APP_URL"
    _USER_VAR = "NP_GITEA_APP_USER"
    _TOKEN_VAR = "NP_GITEA_APP_TOKEN"

    @classmethod
    def create_from_env(cls, ssl_verify: bool = True) -> DataNeuroPolyMTL:
        """Return a :class:`DataNeuroPolyMTL` configured from environment variables."""
        url = os.environ.get(cls._URL_VAR)
        user = os.environ.get(cls._USER_VAR)
        token = os.environ.get(cls._TOKEN_VAR)

        missing = [
            var
            for var, val in [
                (cls._URL_VAR, url),
                (cls._USER_VAR, user),
                (cls._TOKEN_VAR, token),
            ]
            if not val
        ]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

        return DataNeuroPolyMTL(
            url=url,  # type: ignore[arg-type]
            user=user,  # type: ignore[arg-type]
            token=token,  # type: ignore[arg-type]
            ssl_verify=ssl_verify,
        )


class ProviderManagerFactory:
    """Creates provider-backed dataset managers from CLI/env configuration."""

    @staticmethod
    def create(
        provider: str | ProviderName,
        *,
        cache_dir: str | Path | None = None,
        credentials_path: str | Path | None = None,
        token: str | None = None,
        endpoint: str | None = None,
    ):
        try:
            provider_name = (
                provider
                if isinstance(provider, ProviderName)
                else ProviderName(str(provider).lower())
            )
        except (TypeError, ValueError) as exc:
            supported = ", ".join(item.value for item in ProviderName)
            raise ValueError(
                f"Unsupported provider '{provider}'. Supported providers: {supported}."
            ) from exc

        if provider_name == ProviderName.GIT:
            return GitProviderManager(
                repo_url="",
                user=os.environ.get("NP_GIT_USER"),
                token=os.environ.get("NP_GIT_TOKEN"),
                cache_dir=cache_dir,
            )
        if provider_name == ProviderName.KAGGLE:
            return KaggleProviderManager(cache_dir=cache_dir)
        if provider_name == ProviderName.MENDELEY:
            return MendeleyProviderManager(
                access_token=token or os.environ.get("NP_MENDELEY_ACCESS_TOKEN")
            )
        if provider_name == ProviderName.MIDRC:
            return MIDRCProviderManager(
                credentials_path=(
                    str(credentials_path)
                    if credentials_path
                    else os.environ.get("NP_MIDRC_CREDENTIALS")
                ),
                endpoint=endpoint
                or os.environ.get("NP_MIDRC_ENDPOINT", "https://data.midrc.org"),
            )
        if provider_name == ProviderName.OPENNEURO:
            return OpenNeuroProviderManager(cache_dir=cache_dir)
        if provider_name == ProviderName.ZENODO:
            return ZenodoProviderManager(
                token=token or os.environ.get("NP_ZENODO_TOKEN"), cache_dir=cache_dir
            )
        if provider_name == ProviderName.FIGSHARE:
            return FigshareProviderManager(
                token=token or os.environ.get("NP_FIGSHARE_TOKEN")
            )
        raise ValueError(
            f"Unsupported provider '{provider}'. Supported providers: {', '.join(item.value for item in ProviderName)}."
        )


class AnnotationConfigFactory:
    """
    Creates :class:`~npdb.annotation.AnnotationConfig` from CLI arguments.

    All parameters map directly to the fields of :class:`AnnotationConfig`.
    """

    @classmethod
    def create_from_cli_args(
        cls,
        *,
        mode: str | AnnotationMode,
        headless: bool = True,
        timeout: int = 300,
        artifacts_dir: Path | None = None,
        ai_provider: str | None = None,
        ai_model: str | None = None,
        phenotype_dictionary: Path | None = None,
        header_map: Path | None = None,
        dry_run: bool = False,
        keep_annotations: bool = False,
        no_new_columns: bool = False,
    ) -> AnnotationConfig:
        """
        Build an :class:`AnnotationConfig` from keyword arguments that mirror
        the CLI option names.
        """
        mode_value = AnnotationMode(mode) if isinstance(mode, str) else mode
        return AnnotationConfig(
            mode=mode_value,
            headless=headless,
            timeout=timeout,
            artifacts_dir=artifacts_dir,
            ai_provider=ai_provider,
            ai_model=ai_model,
            phenotype_dictionary=phenotype_dictionary,
            header_map=header_map,
            dry_run=dry_run,
            keep_annotations=keep_annotations,
            no_new_columns=no_new_columns,
        )


class AIClientFactory:
    """
    Creates AI client instances for different providers.

    Currently supports ``"ollama"`` and ``"openai"`` (and their variants).
    """

    @classmethod
    def create(cls, provider: str, model: str) -> Any:
        """
        Return an AI client for *provider* / *model*.

        Args:
            provider: One of ``"ollama"``, ``"openai"``, ``"azure_openai"``.
            model:    Model identifier passed to the provider SDK.

        Returns:
            A provider-specific client instance.

        Raises:
            ValueError: for unsupported providers.
            ImportError: if the required provider library is not installed.
        """
        provider_lower = provider.lower()
        if not model:
            raise ValueError(
                f"AI provider '{provider}' requires a non-empty model name."
            )

        if provider_lower == "ollama":
            try:
                import ollama  # type: ignore[import-untyped]
            except ImportError as exc:
                raise ImportError(
                    "The 'ollama' package is required for the Ollama provider. "
                    "Install it with: pip install ollama"
                ) from exc
            return ollama.Client()

        if provider_lower in ("openai", "azure_openai"):
            try:
                import openai  # type: ignore[import-untyped]
            except ImportError as exc:
                raise ImportError(
                    "The 'openai' package is required for the OpenAI provider. "
                    "Install it with: pip install openai"
                ) from exc
            if provider_lower == "azure_openai":
                api_key = os.environ.get("AZURE_OPENAI_API_KEY")
                endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
                missing = [
                    v
                    for v, k in [
                        ("AZURE_OPENAI_API_KEY", api_key),
                        ("AZURE_OPENAI_ENDPOINT", endpoint),
                    ]
                    if not k
                ]
                if missing:
                    raise ValueError(
                        f"Missing Azure OpenAI env vars: {', '.join(missing)}"
                    )
                return openai.AzureOpenAI(api_key=api_key, azure_endpoint=endpoint)
            return openai.OpenAI()

        raise ValueError(
            f"Unsupported AI provider '{provider}'. "
            f"Supported providers: ollama, openai, azure_openai"
        )


class LedgerFactory:
    """Creates :class:`~npdb.ledger.RunLedger` instances."""

    @classmethod
    def create(cls, path: Path | None = None) -> RunLedger:
        """Return a new :class:`RunLedger`, optionally backed by *path*."""
        return RunLedger(path=path)

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

import typer

from npdb.annotation.modes import AnnotationMode
from npdb.factories import GiteaManagerFactory, ProviderManagerFactory
from npdb.managers.model import ProviderName


OPTION_GROUP_NAMES = {
    "input": "Input Options",
    "output": "Output Options",
    "behavior": "Behavior Options",
    "automation": "Automation Options",
    "ai": "AI Options",
    "troubleshooting": "Troubleshooting",
}



bagel = typer.Typer(
    help="Convert BIDS datasets to Neurobagel JSON-LD (from local folders or NeuroGitea).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _provider_call(
    provider: str | ProviderName,
    identifier: str,
    *,
    output: Path,
    online_url: str,
    access_type: str,
    mode: str,
    phenotype_dict: Optional[Path],
    headless: bool,
    timeout: int,
    artifacts_dir: Optional[Path],
    ai_provider: Optional[str],
    ai_model: Optional[str],
    header_map: Optional[Path],
    extend_modalities: bool,
    cache_dir: Optional[Path] = None,
    **kwargs,
) -> None:
    manager = ProviderManagerFactory.create(
        provider,
        cache_dir=str(cache_dir) if cache_dir else None,
        credentials_path=kwargs.get("credentials_path"),
        token=kwargs.get("token"),
        endpoint=kwargs.get("endpoint"),
    )
    provider_id = (
        manager.provider_name.value
        if isinstance(manager.provider_name, ProviderName)
        else str(manager.provider_name)
    )

    local_fetch = Path(output).parent / (
        f"{provider_id}_{Path(identifier).name if hasattr(identifier, 'name') else identifier.replace('/', '_')}"
    )
    fetched = manager.fetch(identifier, local_fetch, **kwargs)

    from npdb.cli.cli import local2bagel

    local2bagel(
        input_dir=fetched,
        online_url=online_url,
        output=output,
        access_type=access_type,
        mode=mode,
        phenotype_dict=phenotype_dict,
        headless=headless,
        timeout=timeout,
        artifacts_dir=artifacts_dir,
        ai_provider=ai_provider,
        ai_model=ai_model,
        header_map=header_map,
        extend_modalities=extend_modalities,
    )


@bagel.command("local")
def local2bagel(
    input_dir: Path = typer.Argument(
        ...,
        help="Local BIDS dataset root directory to convert.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    online_url: str = typer.Argument(
        ...,
        help="Repository URL recorded in output metadata (RepositoryURL/AccessLink).",
    ),
    output: Path = typer.Argument(
        ...,
        help="Output directory for generated Neurobagel files.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    access_type: str = typer.Option(
        "restricted",
        "--access-type",
        help="Access type recorded in output metadata (e.g., 'restricted', 'public').",
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    mode: str = typer.Option(
        AnnotationMode.MANUAL.value,
        help="Annotation mode: manual|assist|auto|full-auto",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    phenotype_dict: Optional[Path] = typer.Option(
        None,
        help="Path to phenotype dictionary JSON for prefill.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    headless: bool = typer.Option(
        True,
        "--headless/--headed",
        help="Run browser in headless mode (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    timeout: int = typer.Option(
        300,
        help="Timeout per step in seconds (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    artifacts_dir: Optional[Path] = typer.Option(
        None,
        help="Directory for screenshots/traces (automation modes).",
        file_okay=False,
        dir_okay=True,
        writable=True,
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    ai_provider: Optional[str] = typer.Option(
        None,
        help="AI provider (e.g., 'ollama').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    ai_model: Optional[str] = typer.Option(
        None,
        help="AI model name (e.g., 'neural-chat').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    header_map: Optional[Path] = typer.Option(
        None,
        "--header-map",
        help="JSON file mapping desired Neurobagel headers to input variants.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    extend_modalities: bool = typer.Option(
        True,
        "--extend-modalities/--neurobagel-modalities",
        help=(
            "Use NeuroPoly custom modality mappings by default. Pass "
            "--neurobagel-modalities to disable extensions and keep Neurobagel "
            "native modality handling only."
        ),
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
):
    """
    [bold]Convert a local BIDS dataset to Neurobagel JSON-LD format[/bold]
    """
    import asyncio

    from npdb.annotation.standardize import load_header_map, validate_header_map_keys
    from npdb.automation.mappings.solvers import load_static_mappings
    from npdb.cli.facade import DatasetConversionFacade
    from npdb.factories import AnnotationConfigFactory

    try:
        mode_enum = AnnotationMode(mode)
    except ValueError:
        typer.echo(f"Error: Invalid mode '{mode}'.", err=True)
        raise typer.Exit(code=1)

    if mode_enum == AnnotationMode.MANUAL and (ai_provider or ai_model):
        typer.echo("Warning: AI options ignored in manual mode.", err=True)
    if ai_provider and not ai_model:
        typer.echo("Error: --ai-model required with --ai-provider.", err=True)
        raise typer.Exit(code=1)
    if ai_model and not ai_provider:
        typer.echo("Error: --ai-provider required with --ai-model.", err=True)
        raise typer.Exit(code=1)

    if header_map:
        try:
            hmap = load_header_map(header_map)
            static = load_static_mappings()
            valid_keys = set(static.get("mappings", {}).keys())
            validate_header_map_keys(hmap, valid_keys)
        except (ValueError, FileNotFoundError) as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(code=1)

    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        typer.echo(f"Error creating output directory '{output}': {e}", err=True)
        raise typer.Exit(code=1)

    if artifacts_dir:
        try:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            typer.echo(
                f"Error creating artifacts directory '{artifacts_dir}': {e}", err=True
            )
            raise typer.Exit(code=1)

    annotation_config = AnnotationConfigFactory.create_from_cli_args(
        mode=mode,
        headless=headless,
        timeout=timeout,
        artifacts_dir=artifacts_dir,
        ai_provider=ai_provider,
        ai_model=ai_model,
        phenotype_dictionary=phenotype_dict,
        header_map=header_map,
    )

    facade = DatasetConversionFacade(annotation_config)
    from npdb.cli.helpers import extend_bids_description

    extend_bids_description(input_dir.name, str(input_dir), online_url, access_type)

    from rich.progress import Progress, SpinnerColumn, TextColumn

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task(f"Converting {input_dir.name}...", total=None)
        try:
            asyncio.run(
                facade.run(input_dir, output, extend_modalities=extend_modalities)
            )
        except Exception as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(code=1)

    typer.echo(f"Conversion complete! Output saved to: {output}")


@bagel.command("gitea")
def gitea2bagel(
    dataset: str = typer.Argument(
        ...,
        help="Dataset name on Gitea (under the datasets organization).",
    ),
    output: Path = typer.Argument(
        ...,
        help="Output directory for generated Neurobagel files.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    verify_ssl: bool = typer.Option(
        True,
        help="Verify SSL certificates when connecting to Gitea.",
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    mode: str = typer.Option(
        AnnotationMode.MANUAL.value,
        help="Annotation mode: manual|assist|auto|full-auto",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    phenotype_dict: Optional[Path] = typer.Option(
        None,
        help="Path to phenotype dictionary JSON for prefill.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    headless: bool = typer.Option(
        True,
        "--headless/--headed",
        help="Run browser in headless mode (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    timeout: int = typer.Option(
        300,
        help="Timeout per step in seconds (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    artifacts_dir: Optional[Path] = typer.Option(
        None,
        help="Directory for screenshots/traces (automation modes).",
        file_okay=False,
        dir_okay=True,
        writable=True,
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    ai_provider: Optional[str] = typer.Option(
        None,
        help="AI provider (e.g., 'ollama').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    ai_model: Optional[str] = typer.Option(
        None,
        help="AI model name (e.g., 'neural-chat').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    header_map: Optional[Path] = typer.Option(
        None,
        "--header-map",
        help="JSON file mapping desired Neurobagel headers to input variants.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    extend_modalities: bool = typer.Option(
        True,
        "--extend-modalities/--neurobagel-modalities",
        help=(
            "Use NeuroPoly custom modality mappings by default. Pass "
            "--neurobagel-modalities to disable extensions and keep Neurobagel "
            "native modality handling only."
        ),
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
):
    """
    [bold]Convert a NeuroGitea dataset to Neurobagel JSON-LD format[/bold]
    """
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

    try:
        gitea_manager = GiteaManagerFactory.create_from_env(ssl_verify=verify_ssl)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    with tempfile.TemporaryDirectory(prefix="npdb_clone_") as tmp_dir:
        local_clone = Path(tmp_dir) / dataset
        gitea_manager.clone_repository(dataset, str(local_clone), light=True)
        url, access_type = gitea_manager.get_description_extensions(dataset)

        local2bagel(
            input_dir=local_clone,
            online_url=url,
            output=output,
            access_type=access_type,
            mode=mode,
            phenotype_dict=phenotype_dict,
            headless=headless,
            timeout=timeout,
            artifacts_dir=artifacts_dir,
            ai_provider=ai_provider,
            ai_model=ai_model,
            header_map=header_map,
            extend_modalities=extend_modalities,
        )


@bagel.command("git")
def git2bagel(
    repository: str = typer.Argument(..., help="Git repository URL or path to clone."),
    output: Path = typer.Argument(
        ...,
        help="Output directory for generated Neurobagel files.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    cache_dir: Optional[Path] = typer.Option(
        None,
        "--cache-dir",
        help="Optional local cache for large datasets. This is not required for repo metadata-only use.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    mode: str = typer.Option(
        AnnotationMode.MANUAL.value,
        help="Annotation mode: manual|assist|auto|full-auto",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    phenotype_dict: Optional[Path] = typer.Option(
        None,
        help="Path to phenotype dictionary JSON for prefill.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    headless: bool = typer.Option(
        True,
        "--headless/--headed",
        help="Run browser in headless mode (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    timeout: int = typer.Option(
        300,
        help="Timeout per step in seconds (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    artifacts_dir: Optional[Path] = typer.Option(
        None,
        help="Directory for screenshots/traces (automation modes).",
        file_okay=False,
        dir_okay=True,
        writable=True,
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    ai_provider: Optional[str] = typer.Option(
        None,
        help="AI provider (e.g., 'ollama').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    ai_model: Optional[str] = typer.Option(
        None,
        help="AI model name (e.g., 'neural-chat').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    header_map: Optional[Path] = typer.Option(
        None,
        "--header-map",
        help="JSON file mapping desired Neurobagel headers to input variants.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    extend_modalities: bool = typer.Option(
        True,
        "--extend-modalities/--neurobagel-modalities",
        help="Use NeuroPoly custom modality mappings by default.",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
):
    _provider_call(
        "git",
        repository,
        output=output,
        online_url=repository,
        access_type="public",
        mode=mode,
        phenotype_dict=phenotype_dict,
        headless=headless,
        timeout=timeout,
        artifacts_dir=artifacts_dir,
        ai_provider=ai_provider,
        ai_model=ai_model,
        header_map=header_map,
        extend_modalities=extend_modalities,
        cache_dir=cache_dir,
    )


@bagel.command("kaggle")
def kaggle2bagel(
    dataset: str = typer.Argument(
        ..., help="Kaggle dataset handle (for example: 'user/dataset_name')."
    ),
    output: Path = typer.Argument(
        ...,
        help="Output directory for generated Neurobagel files.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    cache_dir: Optional[Path] = typer.Option(
        None,
        "--cache-dir",
        help="Required. Local cache for the full Kaggle dataset download; this could be large.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    mode: str = typer.Option(
        AnnotationMode.MANUAL.value,
        help="Annotation mode: manual|assist|auto|full-auto",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    phenotype_dict: Optional[Path] = typer.Option(
        None,
        help="Path to phenotype dictionary JSON for prefill.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    headless: bool = typer.Option(
        True,
        "--headless/--headed",
        help="Run browser in headless mode (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    timeout: int = typer.Option(
        300,
        help="Timeout per step in seconds (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    artifacts_dir: Optional[Path] = typer.Option(
        None,
        help="Directory for screenshots/traces (automation modes).",
        file_okay=False,
        dir_okay=True,
        writable=True,
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    ai_provider: Optional[str] = typer.Option(
        None,
        help="AI provider (e.g., 'ollama').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    ai_model: Optional[str] = typer.Option(
        None,
        help="AI model name (e.g., 'neural-chat').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    header_map: Optional[Path] = typer.Option(
        None,
        "--header-map",
        help="JSON file mapping desired Neurobagel headers to input variants.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    extend_modalities: bool = typer.Option(
        True,
        "--extend-modalities/--neurobagel-modalities",
        help="Use NeuroPoly custom modality mappings by default.",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
):
    _provider_call(
        "kaggle",
        dataset,
        output=output,
        online_url=f"https://www.kaggle.com/datasets/{dataset}",
        access_type="public",
        mode=mode,
        phenotype_dict=phenotype_dict,
        headless=headless,
        timeout=timeout,
        artifacts_dir=artifacts_dir,
        ai_provider=ai_provider,
        ai_model=ai_model,
        header_map=header_map,
        extend_modalities=extend_modalities,
        cache_dir=cache_dir,
    )


@bagel.command("mendeley")
def mendeley2bagel(
    dataset: str = typer.Argument(..., help="Mendeley Data dataset ID or DOI."),
    output: Path = typer.Argument(
        ...,
        help="Output directory for generated Neurobagel files.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    token: Optional[str] = typer.Option(
        None,
        help="Optional Mendeley access token. Or set NP_MENDELEY_ACCESS_TOKEN.",
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    mode: str = typer.Option(
        AnnotationMode.MANUAL.value,
        help="Annotation mode: manual|assist|auto|full-auto",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    phenotype_dict: Optional[Path] = typer.Option(
        None,
        help="Path to phenotype dictionary JSON for prefill.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    headless: bool = typer.Option(
        True,
        "--headless/--headed",
        help="Run browser in headless mode (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    timeout: int = typer.Option(
        300,
        help="Timeout per step in seconds (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    artifacts_dir: Optional[Path] = typer.Option(
        None,
        help="Directory for screenshots/traces (automation modes).",
        file_okay=False,
        dir_okay=True,
        writable=True,
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    ai_provider: Optional[str] = typer.Option(
        None,
        help="AI provider (e.g., 'ollama').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    ai_model: Optional[str] = typer.Option(
        None,
        help="AI model name (e.g., 'neural-chat').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    header_map: Optional[Path] = typer.Option(
        None,
        "--header-map",
        help="JSON file mapping desired Neurobagel headers to input variants.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    extend_modalities: bool = typer.Option(
        True,
        "--extend-modalities/--neurobagel-modalities",
        help="Use NeuroPoly custom modality mappings by default.",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
):
    _provider_call(
        "mendeley",
        dataset,
        output=output,
        online_url=f"https://data.mendeley.com/datasets/{dataset}",
        access_type="restricted",
        mode=mode,
        phenotype_dict=phenotype_dict,
        headless=headless,
        timeout=timeout,
        artifacts_dir=artifacts_dir,
        ai_provider=ai_provider,
        ai_model=ai_model,
        header_map=header_map,
        extend_modalities=extend_modalities,
        token=token,
    )


@bagel.command("midrc")
def midrc2bagel(
    manifest: str = typer.Argument(
        ..., help="MIDRC manifest JSON file or GUID to download."
    ),
    output: Path = typer.Argument(
        ...,
        help="Output directory for generated Neurobagel files.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    credentials_path: Optional[Path] = typer.Option(
        None,
        help="Path to the MIDRC credentials.json file. Or set NP_MIDRC_CREDENTIALS.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    endpoint: Optional[str] = typer.Option(
        None,
        help="MIDRC endpoint. Defaults to https://data.midrc.org.",
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    mode: str = typer.Option(
        AnnotationMode.MANUAL.value,
        help="Annotation mode: manual|assist|auto|full-auto",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    phenotype_dict: Optional[Path] = typer.Option(
        None,
        help="Path to phenotype dictionary JSON for prefill.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    headless: bool = typer.Option(
        True,
        "--headless/--headed",
        help="Run browser in headless mode (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    timeout: int = typer.Option(
        300,
        help="Timeout per step in seconds (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    artifacts_dir: Optional[Path] = typer.Option(
        None,
        help="Directory for screenshots/traces (automation modes).",
        file_okay=False,
        dir_okay=True,
        writable=True,
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    ai_provider: Optional[str] = typer.Option(
        None,
        help="AI provider (e.g., 'ollama').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    ai_model: Optional[str] = typer.Option(
        None,
        help="AI model name (e.g., 'neural-chat').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    header_map: Optional[Path] = typer.Option(
        None,
        "--header-map",
        help="JSON file mapping desired Neurobagel headers to input variants.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    extend_modalities: bool = typer.Option(
        True,
        "--extend-modalities/--neurobagel-modalities",
        help="Use NeuroPoly custom modality mappings by default.",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
):
    _provider_call(
        "midrc",
        manifest,
        output=output,
        online_url="https://data.midrc.org",
        access_type="restricted",
        mode=mode,
        phenotype_dict=phenotype_dict,
        headless=headless,
        timeout=timeout,
        artifacts_dir=artifacts_dir,
        ai_provider=ai_provider,
        ai_model=ai_model,
        header_map=header_map,
        extend_modalities=extend_modalities,
        credentials_path=str(credentials_path) if credentials_path else None,
        endpoint=endpoint,
    )


@bagel.command("openneuro")
def openneuro2bagel(
    dataset: str = typer.Argument(
        ..., help="OpenNeuro dataset ID (for example: ds002799)."
    ),
    output: Path = typer.Argument(
        ...,
        help="Output directory for generated Neurobagel files.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    cache_dir: Optional[Path] = typer.Option(
        None,
        "--cache-dir",
        help="Optional local cache. Default is a temp directory for sparse metadata downloads.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    mode: str = typer.Option(
        AnnotationMode.MANUAL.value,
        help="Annotation mode: manual|assist|auto|full-auto",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    phenotype_dict: Optional[Path] = typer.Option(
        None,
        help="Path to phenotype dictionary JSON for prefill.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    headless: bool = typer.Option(
        True,
        "--headless/--headed",
        help="Run browser in headless mode (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    timeout: int = typer.Option(
        300,
        help="Timeout per step in seconds (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    artifacts_dir: Optional[Path] = typer.Option(
        None,
        help="Directory for screenshots/traces (automation modes).",
        file_okay=False,
        dir_okay=True,
        writable=True,
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    ai_provider: Optional[str] = typer.Option(
        None,
        help="AI provider (e.g., 'ollama').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    ai_model: Optional[str] = typer.Option(
        None,
        help="AI model name (e.g., 'neural-chat').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    header_map: Optional[Path] = typer.Option(
        None,
        "--header-map",
        help="JSON file mapping desired Neurobagel headers to input variants.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    extend_modalities: bool = typer.Option(
        True,
        "--extend-modalities/--neurobagel-modalities",
        help="Use NeuroPoly custom modality mappings by default.",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
):
    _provider_call(
        "openneuro",
        dataset,
        output=output,
        online_url=f"https://openneuro.org/datasets/{dataset}",
        access_type="public",
        mode=mode,
        phenotype_dict=phenotype_dict,
        headless=headless,
        timeout=timeout,
        artifacts_dir=artifacts_dir,
        ai_provider=ai_provider,
        ai_model=ai_model,
        header_map=header_map,
        extend_modalities=extend_modalities,
        cache_dir=cache_dir,
    )


@bagel.command("zenodo")
def zenodo2bagel(
    record: str = typer.Argument(..., help="Zenodo record ID or DOI."),
    output: Path = typer.Argument(
        ...,
        help="Output directory for generated Neurobagel files.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    cache_dir: Optional[Path] = typer.Option(
        None,
        "--cache-dir",
        help="Required when record files are archive-only or the download is large.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    token: Optional[str] = typer.Option(
        None,
        help="Optional Zenodo access token. Or set NP_ZENODO_TOKEN.",
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    mode: str = typer.Option(
        AnnotationMode.MANUAL.value,
        help="Annotation mode: manual|assist|auto|full-auto",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    phenotype_dict: Optional[Path] = typer.Option(
        None,
        help="Path to phenotype dictionary JSON for prefill.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    headless: bool = typer.Option(
        True,
        "--headless/--headed",
        help="Run browser in headless mode (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    timeout: int = typer.Option(
        300,
        help="Timeout per step in seconds (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    artifacts_dir: Optional[Path] = typer.Option(
        None,
        help="Directory for screenshots/traces (automation modes).",
        file_okay=False,
        dir_okay=True,
        writable=True,
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    ai_provider: Optional[str] = typer.Option(
        None,
        help="AI provider (e.g., 'ollama').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    ai_model: Optional[str] = typer.Option(
        None,
        help="AI model name (e.g., 'neural-chat').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    header_map: Optional[Path] = typer.Option(
        None,
        "--header-map",
        help="JSON file mapping desired Neurobagel headers to input variants.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    extend_modalities: bool = typer.Option(
        True,
        "--extend-modalities/--neurobagel-modalities",
        help="Use NeuroPoly custom modality mappings by default.",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
):
    _provider_call(
        "zenodo",
        record,
        output=output,
        online_url=f"https://zenodo.org/record/{record}",
        access_type="public",
        mode=mode,
        phenotype_dict=phenotype_dict,
        headless=headless,
        timeout=timeout,
        artifacts_dir=artifacts_dir,
        ai_provider=ai_provider,
        ai_model=ai_model,
        header_map=header_map,
        extend_modalities=extend_modalities,
        cache_dir=cache_dir,
        token=token,
    )


@bagel.command("figshare")
def figshare2bagel(
    article: str = typer.Argument(..., help="Figshare article ID or DOI."),
    output: Path = typer.Argument(
        ...,
        help="Output directory for generated Neurobagel files.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    token: Optional[str] = typer.Option(
        None,
        help="Optional Figshare access token. Or set NP_FIGSHARE_TOKEN.",
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    mode: str = typer.Option(
        AnnotationMode.MANUAL.value,
        help="Annotation mode: manual|assist|auto|full-auto",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    phenotype_dict: Optional[Path] = typer.Option(
        None,
        help="Path to phenotype dictionary JSON for prefill.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    headless: bool = typer.Option(
        True,
        "--headless/--headed",
        help="Run browser in headless mode (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    timeout: int = typer.Option(
        300,
        help="Timeout per step in seconds (automation modes).",
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    artifacts_dir: Optional[Path] = typer.Option(
        None,
        help="Directory for screenshots/traces (automation modes).",
        file_okay=False,
        dir_okay=True,
        writable=True,
        rich_help_panel=OPTION_GROUP_NAMES["automation"],
    ),
    ai_provider: Optional[str] = typer.Option(
        None,
        help="AI provider (e.g., 'ollama').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    ai_model: Optional[str] = typer.Option(
        None,
        help="AI model name (e.g., 'neural-chat').",
        rich_help_panel=OPTION_GROUP_NAMES["ai"],
    ),
    header_map: Optional[Path] = typer.Option(
        None,
        "--header-map",
        help="JSON file mapping desired Neurobagel headers to input variants.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    extend_modalities: bool = typer.Option(
        True,
        "--extend-modalities/--neurobagel-modalities",
        help="Use NeuroPoly custom modality mappings by default.",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
):
    _provider_call(
        "figshare",
        article,
        output=output,
        online_url=f"https://figshare.com/articles/{article}",
        access_type="public",
        mode=mode,
        phenotype_dict=phenotype_dict,
        headless=headless,
        timeout=timeout,
        artifacts_dir=artifacts_dir,
        ai_provider=ai_provider,
        ai_model=ai_model,
        header_map=header_map,
        extend_modalities=extend_modalities,
        token=token,
    )

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.live import Live
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from npdb.annotation.modes import AnnotationMode
from npdb.cli.display import RepoDownloadDisplay
from npdb.cli.helpers import (
    extend_bids_description,
    fetch_url,
    is_http_url,
    looks_like_non_git_repo_error,
    read_tsv,
    repo_has_git_annex,
)
from npdb.factories import GiteaManagerFactory, ProviderManagerFactory
from npdb.managers.git import GitProviderManager
from npdb.managers.midrc import MIDRCProviderManager
from npdb.managers.model import ProviderName

OPTION_GROUP_NAMES = {
    "input": "Input Options",
    "output": "Output Options",
    "behavior": "Behavior Options",
    "automation": "Automation Options",
    "ai": "AI Options",
    "troubleshooting": "Troubleshooting",
}


npdb = typer.Typer(
    help="NeuroPoly Database CLI for converting, standardizing, and downloading BIDS datasets.",
    context_settings={"help_option_names": ["--help", "-h"]},
    no_args_is_help=True,
    rich_markup_mode="rich",
    epilog="Run 'npdb COMMAND --help' for more information on a command.",
)


@npdb.callback()
def main():
    return


convert = typer.Typer(
    help=(
        "Dataset conversion workflows for standardizing and ingesting BIDS data "
        "into the Neurobagel JSON-LD format."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)
npdb.add_typer(convert, name="convert")


bagel = typer.Typer(
    help=(
        "Convert dataset metadata into Neurobagel JSON-LD. The pipeline downloads or "
        "loads the source dataset, normalizes the tabular metadata, and writes the "
        "resulting Neurobagel output. Dataset access requirements vary by backend; "
        "see the provider guide in docs/npdb/provider_managers.md and the "
        "environment template in template.env."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)
convert.add_typer(bagel, name="bagel")


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
    [bold]Convert local dataset metadata to Neurobagel JSON-LD[/bold]

    Use this when the dataset is already on disk and you need to convert the
    tabular metadata into Neurobagel format. The command reads the dataset's
    metadata files, standardizes them, resolves phenotype mappings, and writes
    the final Neurobagel output in the selected directory.

    This flow does not require remote credentials for a local dataset. Access is
    governed by the dataset's local filesystem permissions only.

    Annotation modes:
    * [cyan]manual[/cyan]: interactive annotation and review
    * [cyan]assist[/cyan]: browser-assisted annotation with user confirmation
    * [cyan]auto[/cyan]: automated annotation using model suggestions
    * [cyan]full-auto[/cyan]: fully unattended conversion (use with caution)
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
    extend_bids_description(input_dir.name, str(input_dir), online_url, access_type)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task(f"Converting {input_dir.name}...", total=None)
        try:
            asyncio.run(
                facade.run(
                    input_dir,
                    output,
                    extend_modalities=extend_modalities,
                )
            )
        except Exception as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(code=1)

    typer.echo(f"Conversion complete! Output saved to: {output}")


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

    dataset_id = (
        Path(identifier).name
        if hasattr(identifier, "name")
        else identifier.replace("/", "_")
    )
    dataset_id = dataset_id.replace(".", "_")
    if isinstance(manager, GitProviderManager):
        dataset_id = manager.dataset_id(identifier).replace("/", "_")
    if isinstance(manager, MIDRCProviderManager):
        dataset_id = manager.discovery_id(identifier) or dataset_id
        online_url, access_type = manager.describe(identifier)
    local_fetch = Path(output) / f"{provider_id}_{dataset_id}"

    if isinstance(manager, MIDRCProviderManager):
        display = RepoDownloadDisplay()
        manager.add_download_observer(display)
        with Live(display, refresh_per_second=4, transient=False):
            fetched = manager.fetch(identifier, local_fetch, **kwargs)
    else:
        fetched = manager.fetch(identifier, local_fetch, **kwargs)

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
    [bold]Convert NeuroGitea dataset metadata to Neurobagel JSON-LD[/bold]

    This command resolves a dataset from the configured neurogitea instance,
    clones it locally, and then runs the metadata conversion flow: metadata
    normalization, phenotype resolution, and Neurobagel export.

    Access requirements:
    * Requires the Gitea/Forgejo connection variables in .env or the environment:
      [cyan]NP_GITEA_APP_URL[/cyan], [cyan]NP_GITEA_APP_USER[/cyan],
      [cyan]NP_GITEA_APP_TOKEN[/cyan].
    * Private or restricted datasets may also require the instance-specific SSH
      or token access described in the Gitea setup docs.
    * If the dataset itself is public, token-based authentication is often not
      required beyond repository access policies.
    """
    from npdb.factories import GiteaManagerFactory

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

    try:
        gitea_manager = GiteaManagerFactory.create_from_env(ssl_verify=verify_ssl)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    with tempfile.TemporaryDirectory(prefix="npdb_clone_") as tmp_dir:
        local_clone = Path(tmp_dir) / dataset

        # 1. Clone the repository
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
    """
    [bold]Convert Git repository metadata to Neurobagel JSON-LD[/bold]

    This command clones or reuses a Git repository, stages the dataset locally,
    and then runs the metadata conversion sequence used by the other Bagel
    providers: metadata normalization, phenotype standardization, and output
    export.

    Access requirements:
    * Public repositories are usually usable without credentials.
    * Private repositories may require Git HTTP credentials via
      [cyan]NP_GIT_USER[/cyan] and [cyan]NP_GIT_TOKEN[/cyan].
    * Large repositories or archive-heavy datasets should use a persistent
      [cyan]--cache-dir[/cyan] so the local staging step is not exhausted by a
      temporary download location.
    * See the provider documentation in [cyan]docs/npdb/provider_managers.md[/cyan]
      and the environment template in [cyan]template.env[/cyan].
    """
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
    """
    [bold]Convert Kaggle dataset metadata to Neurobagel JSON-LD[/bold]

    This command downloads the Kaggle dataset into a local cache or working
    directory, stages the dataset locally, and converts its metadata into the
    Neurobagel format.

    Access requirements:
    * Public Kaggle datasets may work without authentication.
    * Private datasets require [cyan]NP_KAGGLE_USERNAME[/cyan] and
      [cyan]NP_KAGGLE_KEY[/cyan].
    * Large downloads should use [cyan]--cache-dir[/cyan] because the dataset may be
      sizable and may require long staging time.
    """
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
    """
    [bold]Convert Mendeley dataset metadata to Neurobagel JSON-LD[/bold]

    Download the dataset from Mendeley, stage it locally, and convert the
    metadata records into the Neurobagel format. The command is useful when a
    dataset is exposed through a public or controlled Mendeley archive.

    Access requirements:
    * Public Mendeley archives may work without authentication.
    * Restricted or access-controlled datasets usually require an access token or
      app credentials, via [cyan]NP_MENDELEY_ACCESS_TOKEN[/cyan] and the related
      Mendeley client variables.
    * See the provider guide and the template environment file for the required
      variables.
    """
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
        ..., help="MIDRC Discovery URL or dataset ID, file GUID, or manifest JSON path."
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
    cache_dir: Optional[Path] = typer.Option(
        None,
        "--cache-dir",
        help="Persistent download cache (required for Discovery datasets). Or set NP_NPDB_CACHE_DIR.",
        file_okay=False,
        dir_okay=True,
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
    """
    [bold]Convert MIDRC dataset metadata to Neurobagel JSON-LD[/bold]

    Resolve a Discovery dataset, file GUID, or Gen3 manifest and download its
    files using authorized URLs. Archives and layouts use the shared provider
    preparation pipeline before Neurobagel conversion; unsupported layouts fail
    explicitly rather than being remapped using dataset-specific rules.

    Access requirements:
    * Restricted MIDRC data requires credentials from the MIDRC portal.
    * Provide the credentials file through [cyan]NP_MIDRC_CREDENTIALS[/cyan] or the
      [cyan]--credentials-path[/cyan] option.
    * The endpoint is usually the default MIDRC endpoint unless you are using a
      custom instance.
    * Discovery datasets download all linked files and require a persistent cache.
    """
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
        cache_dir=cache_dir,
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
    """
    [bold]Convert OpenNeuro dataset metadata to Neurobagel JSON-LD[/bold]

    OpenNeuro is a common public BIDS repository, so this command is often used
    for publicly accessible datasets. Once the dataset is staged locally, the
    command converts the metadata records into Neurobagel format.

    Access requirements:
    * Public datasets usually work without credentials.
    * Restricted or private datasets may require [cyan]NP_OPENNEURO_TOKEN[/cyan].
    * Use [cyan]--cache-dir[/cyan] when downloads are large or when a persistent
      local staging area is preferable.
    """
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
    """
    [bold]Convert Zenodo record metadata to Neurobagel JSON-LD[/bold]

    This command resolves a Zenodo record or DOI, downloads the archive or files
    into a working directory, and converts the metadata into Neurobagel format.
    It is particularly useful for public records and for datasets with a large or
    archive-only payload.

    Access requirements:
    * Public records often work without a token.
    * Embargoed, restricted, or archive-only records may require
      [cyan]NP_ZENODO_TOKEN[/cyan].
    * Large downloads should use [cyan]--cache-dir[/cyan] and the local cache is
      strongly recommended for archive-based datasets.
    """
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
    """
    [bold]Convert Figshare article metadata to Neurobagel JSON-LD[/bold]

    This command resolves a Figshare article or DOI, stages the content locally,
    and converts the metadata into Neurobagel format using the same provider
    workflow used by the other Bagel commands.

    Access requirements:
    * Public Figshare content usually works without credentials.
    * Private, embargoed, or restricted content may require
      [cyan]NP_FIGSHARE_TOKEN[/cyan].
    * Large article bundles should prefer a persistent [cyan]--cache-dir[/cyan].
    * See the provider guide and template.env for the full authentication setup.
    """
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


@npdb.command("download")
def download(
    query_results: Path = typer.Argument(
        ...,
        help="Path to query-results TSV exported from Neurobagel Query.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    derivatives: bool = typer.Option(
        True,
        help="Download derivatives associated to the raw input data in each repository (git mode only).",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    output_dir: Path = typer.Option(
        Path.cwd(),
        "--output-dir",
        "-o",
        help="Directory to save downloaded files.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
    max_workers: int = typer.Option(
        4,
        "--max-workers",
        help="Maximum parallel HTTP downloads.",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    verify_ssl: bool = typer.Option(
        True,
        help="Verify SSL certificates when connecting to Gitea (git mode only).",
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Print each git command before it runs (git mode only).",
        rich_help_panel=OPTION_GROUP_NAMES["troubleshooting"],
    ),
):
    """
    [bold]Download imaging data from query results TSV[/bold]

    This command reads a TSV file containing query results obtained from Neurobagel Query
    and automatically selects the download protocol per dataset:

    * [cyan]HTTP:[/cyan] If [bold]AccessLink[/bold] is present for the dataset, download from
      link(s) directly.
    * [cyan]Git:[/cyan] Otherwise, clone from [bold]RepositoryURL[/bold] with sparse checkout.
    * [cyan]Git-annex:[/cyan] If the repository exposes a [bold]git-annex[/bold] branch,
      run annex content retrieval after git checkout.
    * [magenta]Other protocols and hosts to come[/magenta]

    Backend selection is automatic and can differ by dataset within the same TSV.

    Git operations require [bold]NP_GITEA_APP_URL[/bold], [bold]NP_GITEA_APP_USER[/bold],
    and [bold]NP_GITEA_APP_TOKEN[/bold] environment variables.
    """
    try:
        rows = read_tsv(query_results)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error reading TSV: {exc}", err=True)
        raise typer.Exit(code=1)

    output_dir.mkdir(parents=True, exist_ok=True)

    datasets_with_http: set[str] = set()
    seen_urls: set[str] = set()
    http_jobs: list[tuple[str, Path, str, str]] = []

    for row in rows:
        dataset = (row.get("DatasetName") or "unknown").strip()
        subject = (row.get("SubjectID") or "unknown").strip()
        url = (row.get("AccessLink") or "").strip()
        if not is_http_url(url):
            continue
        datasets_with_http.add(dataset)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        filename = os.path.basename(url.split("?")[0]) or f"{subject}.bin"
        dest = output_dir / dataset / subject / filename
        http_jobs.append((url, dest, dataset, subject))

    http_failures = 0
    if http_jobs:
        typer.echo(
            f"Downloading {len(http_jobs)} file(s) via HTTP ({max_workers} workers)..."
        )
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(fetch_url, url, dest): (dataset, subject)
                for url, dest, dataset, subject in http_jobs
            }
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                transient=True,
            ) as progress:
                task = progress.add_task(
                    "Downloading HTTP links...", total=len(futures)
                )
                for future in as_completed(futures):
                    ok, msg = future.result()
                    dataset, subject = futures[future]
                    typer.echo(
                        f"{'SUCCESS' if ok else 'FAIL'} {dataset}/{subject}: {msg}"
                    )
                    if not ok:
                        http_failures += 1
                    progress.advance(task)

        typer.echo("HTTP download phase complete.")

    subjects: list[tuple[str, str, str]] = []
    for row in rows:
        dataset = (row.get("DatasetName") or "unknown").strip()
        if dataset in datasets_with_http:
            continue
        repo_url = (row.get("RepositoryURL") or "").strip()
        imaging_path = (row.get("ImagingSessionPath") or "").strip()
        if not repo_url or not imaging_path:
            continue
        subjects.append((repo_url, imaging_path, dataset))

    git_failures = 0
    if subjects:
        load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

        try:
            if verbose:
                typer.echo("Initializing Gitea manager...")
            gitea_manager = GiteaManagerFactory.create_from_env(ssl_verify=verify_ssl)
            if verbose:
                typer.echo("Gitea manager initialized successfully.")
        except ValueError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(code=1)

        gitea_manager.verbose = verbose

        grouped: dict[tuple[str, str], list[str]] = {}
        for repo_url, sparse_path, dataset in subjects:
            key = (repo_url, dataset)
            if key not in grouped:
                grouped[key] = []
            if sparse_path not in grouped[key]:
                grouped[key].append(sparse_path)

        typer.echo(
            "Downloading via git auto-selection "
            f"({len(subjects)} paths across {len(grouped)} repo(s))..."
        )

        display = RepoDownloadDisplay()
        gitea_manager.add_download_observer(display)

        with Live(display, refresh_per_second=4, transient=False):
            for (repo_url, dataset), sparse_paths in grouped.items():
                use_annex = repo_has_git_annex(gitea_manager, repo_url)
                if verbose:
                    protocol = "git + git-annex" if use_annex else "git"
                    typer.echo(f"Protocol for {dataset}: {protocol}")

                results = gitea_manager.download_subjects(
                    [(repo_url, p, dataset) for p in sparse_paths],
                    output_dir,
                    use_annex=use_annex,
                    derivatives=derivatives,
                )

                for ok, label, message in results:
                    if ok:
                        continue
                    git_failures += 1
                    if looks_like_non_git_repo_error(message):
                        typer.echo(
                            f"FAIL {label}: RepositoryURL is not a git repository.",
                            err=True,
                        )
                    else:
                        typer.echo(f"FAIL {label}: {message}", err=True)

    if not http_jobs and not subjects:
        typer.echo(
            "Warning: No download targets found in TSV (no valid AccessLink or git imaging rows).",
            err=True,
        )
        return

    if git_failures or http_failures:
        typer.echo(
            f"Download completed with failures (HTTP: {http_failures}, git: {git_failures}).",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo("Download complete!")


standardize = typer.Typer(
    help="Standardization tools for BIDS datasets.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
npdb.add_typer(standardize, name="standardize")


@standardize.command("bids")
def standardize_bids(
    bids_dir: Path = typer.Argument(
        ...,
        help="Path to BIDS dataset root (must contain participants.tsv).",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    mode: str = typer.Option(
        AnnotationMode.MANUAL.value,
        help="Annotation mode: manual|auto|full-auto (assist is not supported here)",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print changes to terminal without writing files.",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    no_new_columns: bool = typer.Option(
        False,
        "--no-new-columns",
        help="Don't add missing standard columns (e.g., age, sex).",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    keep_annotations: bool = typer.Option(
        False,
        "--keep-annotations",
        help="Include Neurobagel Annotations block in participants.json.",
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
        help="JSON file mapping desired headers to input variants.",
        exists=True,
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
):
    """
    [bold]Standardize BIDS dataset participants.tsv and participants.json[/bold]

    Renames column headers to canonical BIDS names, adds missing standard
    columns, and generates a BIDS-compliant participants.json sidecar.

    Edits the dataset in-place. Use [cyan]--dry-run[/cyan] to preview changes
    without writing files.
    """
    import asyncio

    from npdb.cli.facade import BIDSStandardizationFacade
    from npdb.factories import AnnotationConfigFactory

    try:
        mode_enum = AnnotationMode(mode)
    except ValueError:
        typer.echo(f"Error: Invalid mode '{mode}'.", err=True)
        raise typer.Exit(code=1)

    if mode_enum == AnnotationMode.ASSIST:
        typer.echo(
            "Error: --mode assist is no longer supported for 'npdb standardize bids'. "
            "Use one of: manual, auto, full-auto.",
            err=True,
        )
        raise typer.Exit(code=1)

    if mode_enum == AnnotationMode.MANUAL and (ai_provider or ai_model):
        typer.echo("Warning: AI options ignored in manual mode.", err=True)

    if ai_provider and not ai_model:
        typer.echo("Error: --ai-model required with --ai-provider.", err=True)
        raise typer.Exit(code=1)
    if ai_model and not ai_provider:
        typer.echo("Error: --ai-provider required with --ai-model.", err=True)
        raise typer.Exit(code=1)

    participants_tsv = bids_dir / "participants.tsv"
    if not participants_tsv.exists():
        typer.echo(f"Error: participants.tsv not found in {bids_dir}.", err=True)
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo("Dry-run mode: no files will be modified.\n")

    config = AnnotationConfigFactory.create_from_cli_args(
        mode=mode,
        headless=headless,
        timeout=timeout,
        artifacts_dir=artifacts_dir,
        ai_provider=ai_provider,
        ai_model=ai_model,
        phenotype_dictionary=phenotype_dict,
        dry_run=dry_run,
        keep_annotations=keep_annotations,
        header_map=header_map,
        no_new_columns=no_new_columns,
    )

    facade = BIDSStandardizationFacade(config)

    try:
        asyncio.run(facade.run(bids_dir))
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"Error during BIDS standardization: {e}", err=True)
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo("\nDry-run complete. No files were modified.")
    else:
        typer.echo(f"\nBIDS standardization complete: {bids_dir}")

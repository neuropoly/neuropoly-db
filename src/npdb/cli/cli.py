import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.live import Live
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
)

from npdb.annotation.modes import AnnotationMode
from npdb.cli.display import RepoDownloadDisplay
from npdb.cli.helpers import (
    OPTION_GROUP_NAMES,
    extend_bids_description,
    fetch_url,
    help_option,
    is_http_url,
    looks_like_non_git_repo_error,
    read_tsv,
    repo_has_git_annex,
)
from npdb.factories import GitManagerFactory
from npdb.managers.neuropoly import DataNeuroPolyMTL

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
    help="Conversion commands for neuroimaging dataset metadata and formats.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
npdb.add_typer(convert, name="convert")


bagel = typer.Typer(
    help="Convert BIDS datasets to Neurobagel JSON-LD (from local folders or NeuroGitea).",
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
    help_: bool = help_option(),
):
    """
    [bold]Convert a local BIDS dataset to Neurobagel JSON-LD format[/bold]

    This command automates annotation of phenotypic data using the selected mode:
    * [cyan]manual[/cyan]: Interactive annotation tool
    * [cyan]assist[/cyan]: Browser automation with user confirmation
    * [cyan]auto[/cyan]: Fully automated with ML-based suggestions
    * [cyan]full-auto[/cyan]: Experimental unattended mode (requires review!)
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
    help_: bool = help_option(),
):
    """
    [bold]Convert a NeuroGitea dataset to Neurobagel JSON-LD format[/bold]

    This command automates annotation of phenotypic data using the selected mode:
    * [cyan]manual[/cyan]: Interactive annotation tool
    * [cyan]assist[/cyan]: Browser automation with user confirmation
    * [cyan]auto[/cyan]: Fully automated with ML-based suggestions
    * [cyan]full-auto[/cyan]: Experimental unattended mode (requires review!)

    The dataset is cloned from NeuroGitea first, then converted via the local
    conversion pipeline.
    """
    from dotenv import load_dotenv

    from npdb.factories import GitManagerFactory

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

    try:
        gitea_manager = GitManagerFactory.create_from_env(
            repo_url=os.environ.get("NP_GITEA_APP_URL", ""),
            ssl_verify=verify_ssl
        )
        if not isinstance(gitea_manager, DataNeuroPolyMTL):
            raise ValueError("The Gitea command requires a NeuroData manager")
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
    help_: bool = help_option(),
):
    """
    [bold]Download imaging data from query results TSV[/bold]

        This command reads a TSV file containing query results and automatically
        selects the download protocol per dataset:

    * [cyan]HTTP:[/cyan] If [bold]AccessLink[/bold] is present for the dataset, download from
      link(s) directly.
    * [cyan]Git:[/cyan] Otherwise, clone from [bold]RepositoryURL[/bold] with sparse checkout.
    * [cyan]Git-annex:[/cyan] If the repository exposes a [bold]git-annex[/bold] branch,
      run annex content retrieval after git checkout.

        Backend selection is automatic and can differ by dataset within the same TSV.

    GitHub downloads do not require Gitea credentials. NeuroData downloads require
    [bold]NP_GITEA_APP_URL[/bold], [bold]NP_GITEA_APP_USER[/bold], and
    [bold]NP_GITEA_APP_TOKEN[/bold].
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

        with Live(display, refresh_per_second=4, transient=False):
            for (repo_url, dataset), sparse_paths in grouped.items():
                try:
                    if verbose:
                        typer.echo(f"Initializing git manager for {repo_url}...")
                    git_manager = GitManagerFactory.create_from_env(
                        repo_url=repo_url,
                        ssl_verify=verify_ssl,
                    )
                except ValueError as e:
                    typer.echo(f"Error: {e}", err=True)
                    git_failures += 1
                    continue

                git_manager.verbose = verbose
                git_manager.add_download_observer(display)

                use_annex = repo_has_git_annex(git_manager, repo_url)
                if verbose:
                    protocol = "git + git-annex" if use_annex else "git"
                    typer.echo(f"Protocol for {dataset}: {protocol}")

                results = git_manager.download_subjects(
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
    help_: bool = help_option(),
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

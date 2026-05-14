import asyncio
import csv
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from threading import Lock

import httpx
import typer
from dotenv import load_dotenv
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)

from npdb.annotation.standardize import load_header_map, validate_header_map_keys
from npdb.automation.mappings.solvers import load_static_mappings
from npdb.cli.facade import BIDSStandardizationFacade, DatasetConversionFacade
from npdb.factories import AnnotationConfigFactory, GiteaManagerFactory

OPTION_GROUP_NAMES = {
    "input": "Input Options",
    "output": "Output Options",
    "behavior": "Behavior Options",
    "automation": "Automation Options",
    "ai": "AI Options",
    "troubleshooting": "Troubleshooting",
}


def show_help(ctx: typer.Context, value: bool):
    if value:
        typer.echo(ctx.get_help())
        raise typer.Exit()


def help_option():
    return typer.Option(
        False,
        "--help",
        "-h",
        callback=show_help,
        help="Show this message and exit.",
        rich_help_panel=OPTION_GROUP_NAMES["troubleshooting"],
    )


npdb = typer.Typer(
    help="Conversion tools and utilities for NeuroPoly Database (BIDS)",
    context_settings={"help_option_names": ["--help", "-h"]},
    no_args_is_help=True,
    rich_markup_mode="rich",
    epilog="Run 'npdb COMMAND --help' for more information on a command.",
)


@npdb.callback()
def main():
    return


@npdb.command()
def gitea2bagel(
    dataset: str = typer.Argument(
        ...,
        help="Dataset name on Gitea (under the datasets organization).",
    ),
    output: Path = typer.Argument(
        ...,
        help="Output directory for converted dataset.",
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
        "manual",
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
    help_: bool = help_option(),
):
    """
    [bold]Convert a BIDS dataset from Gitea to Neurobagel JSON-LD format[/bold]

    This command automates annotation of phenotypic data using the selected mode:
    * [cyan]manual[/cyan]: Interactive annotation tool
    * [cyan]assist[/cyan]: Browser automation with user confirmation
    * [cyan]auto[/cyan]: Fully automated with ML-based suggestions
    * [cyan]full-auto[/cyan]: Experimental unattended mode (requires review!)
    """
    if mode not in ["manual", "assist", "auto", "full-auto"]:
        typer.echo(f"Error: Invalid mode '{mode}'.", err=True)
        raise typer.Exit(code=1)

    if mode == "manual" and (ai_provider or ai_model):
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

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

    try:
        gitea_manager = GiteaManagerFactory.create_from_env(ssl_verify=verify_ssl)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
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

    facade = DatasetConversionFacade(gitea_manager, annotation_config)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task(f"Converting {dataset}...", total=None)
        try:
            asyncio.run(facade.run(dataset, output))
        except Exception as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(code=1)

    typer.echo(f"Conversion complete! Output saved to: {output}")


def _read_download_tsv(tsv_path: Path) -> list[dict]:
    with open(tsv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError("TSV file is empty or has no header row")
        rows = list(reader)
    if not rows:
        raise ValueError("TSV file contains no data rows")
    return rows


def _fetch_url(url: str, dest: Path, timeout: int = 300) -> tuple[bool, str]:
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as r:
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        return True, f"Downloaded: {dest.name}"
    except Exception as exc:
        return False, str(exc)


@npdb.command("download")
def download(
    query_results: Path = typer.Argument(
        ...,
        help="Path to query results TSV file with AccessLink column.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    git: bool = typer.Option(
        False,
        "--git",
        help="Download using git (for datasets indexed on git).",
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    git_annex: bool = typer.Option(
        False,
        "--git-annex",
        help="Use git-annex for downloading files (for large datasets indexed on git).",
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
        help="Maximum parallel downloads (URL mode only).",
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

    This command reads a TSV file containing query results and downloads the
    associated imaging data using one of three modes:

    * [cyan]URL mode (default):[/cyan] Reads the [bold]AccessLink[/bold] column and downloads
      each URL in parallel using HTTP.
    * [cyan]Git mode[/cyan] ([bold]--git[/bold]): Performs an authenticated shallow sparse-checkout
      clone from the [bold]RepositoryURL[/bold] column.  Requires [bold]NP_GITEA_APP_URL[/bold],
      [bold]NP_GITEA_APP_USER[/bold] and [bold]NP_GITEA_APP_TOKEN[/bold] environment variables.
    * [cyan]Git-annex mode[/cyan] ([bold]--git[/bold] [bold]--git-annex[/bold]): Same as git mode, but
      also runs [bold]git annex get[/bold] after cloning.
    """
    if git_annex and not git:
        typer.echo("Error: --git-annex requires --git.", err=True)
        raise typer.Exit(code=1)

    try:
        rows = _read_download_tsv(query_results)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error reading TSV: {exc}", err=True)
        raise typer.Exit(code=1)

    output_dir.mkdir(parents=True, exist_ok=True)

    if not git:
        seen_urls: set[str] = set()
        jobs: list[tuple[str, Path, str, str]] = []
        for row in rows:
            url = (row.get("AccessLink") or "").strip()
            if not url or not url.startswith(("http://", "https://")):
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            dataset = (row.get("DatasetName") or "unknown").strip()
            subject = (row.get("SubjectID") or "unknown").strip()
            filename = os.path.basename(url.split("?")[0]) or f"{subject}.bin"
            dest = output_dir / dataset / subject / filename
            jobs.append((url, dest, dataset, subject))

        if not jobs:
            typer.echo("Warning: No valid AccessLink URLs found in TSV.", err=True)
            return

        typer.echo(f"Downloading {len(jobs)} file(s) ({max_workers} workers)...")
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_fetch_url, url, dest): (dataset, subject)
                for url, dest, dataset, subject in jobs
            }
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                transient=True,
            ) as progress:
                task = progress.add_task("Downloading...", total=len(futures))
                for future in as_completed(futures):
                    ok, msg = future.result()
                    dataset, subject = futures[future]
                    typer.echo(
                        f"{'SUCCESS' if ok else 'FAIL'} {dataset}/{subject}: {msg}"
                    )
                    progress.advance(task)

        typer.echo("Download complete!")
        return

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

    try:
        gitea_manager = GiteaManagerFactory.create_from_env(ssl_verify=verify_ssl)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    gitea_manager.verbose = verbose

    subjects: list[tuple[str, str, str]] = []
    for row in rows:
        repo_url = (row.get("RepositoryURL") or "").strip()
        imaging_path = (row.get("ImagingSessionPath") or "").strip()
        dataset = (row.get("DatasetName") or "unknown").strip()
        if not repo_url or not imaging_path:
            continue
        subjects.append((repo_url, imaging_path, dataset))

    if not subjects:
        typer.echo(
            "Warning: No rows with both RepositoryURL and ImagingSessionPath found.",
            err=True,
        )
        return

    mode_label = "git + git-annex" if git_annex else "git sparse-checkout"
    unique_repos = len({(r, d) for r, _, d in subjects})
    typer.echo(
        f"Downloading via {mode_label} ({len(subjects)} paths across {unique_repos} repo(s))..."
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        transient=True,
        expand=True,
    ) as progress:
        # Prepare callbacks for progress display
        step_callback = None  # type: ignore
        annex_progress_callback = None  # type: ignore

        if git_annex:
            # Git-annex mode: per-file progress bars

            # Track tasks by filename to update dynamically
            file_tasks: dict[str, int] = {}
            lock = Lock()

            def step_callback(description: str, step_ix: int, step_total: int) -> None:  # type: ignore
                """Callback for clone/checkout steps."""
                with lock:
                    # Update or create a spinner task for steps
                    if not hasattr(step_callback, "step_task_id"):
                        step_callback.step_task_id = progress.add_task(
                            f"[cyan]{description}", total=None
                        )
                    else:
                        progress.update(
                            step_callback.step_task_id,
                            description=f"[cyan]{description}",
                            total=step_total,
                            completed=step_ix,
                        )

            def annex_progress_callback(
                file: str, pct: float, bytes_done: int, bytes_total: int
            ) -> None:
                """Callback for per-file annex progress."""
                with lock:
                    if file not in file_tasks:
                        # Create a new task for this file
                        file_tasks[file] = progress.add_task(
                            f"  {file}", total=bytes_total
                        )
                    # Update the task with progress
                    task_id = file_tasks[file]
                    progress.update(task_id, completed=bytes_done)  # type: ignore
                    if pct >= 100.0:
                        # Mark as complete
                        pass

        else:
            # Git-only mode: spinner with step names
            lock = Lock()
            repo_tasks: dict[str, int] = {}
            repo_counter = {"current": 0}
            groups: dict[tuple[str, str], list[str]] = {}
            for repo_url, sparse_path, dataset_name in subjects:
                key = (repo_url, dataset_name)
                groups.setdefault(key, [])
                if sparse_path not in groups[key]:
                    groups[key].append(sparse_path)

            def step_callback(
                description: str,
                step_ix: int | None = None,
                step_total: int | None = None,
            ) -> None:
                """Callback for clone/checkout steps."""
                with lock:
                    if not hasattr(step_callback, "current_repo"):
                        step_callback.current_repo = None
                        step_callback.repo_tasks = {}
                    # Try to extract repo name from description or increment counter
                    repo_idx = repo_counter["current"]
                    if repo_idx not in repo_tasks:
                        # Create task for this repo
                        repo_tasks[repo_idx] = progress.add_task(  # type: ignore
                            f"[repo {repo_idx}]: {description}",
                            total=step_total,
                            completed=step_ix,  # type: ignore
                        )
                    else:
                        # Update existing task
                        progress.update(
                            repo_tasks[repo_idx],  # type: ignore
                            description=f"[repo {repo_idx}]: {description}",
                        )
                    # If we see "Checking out files...", increment counter for next repo
                    if "Checking out" in description:
                        repo_counter["current"] += 1

        results = gitea_manager.download_subjects(
            subjects,
            output_dir,
            use_annex=git_annex,
            git_step_callback=step_callback,
            annex_progress_callback=annex_progress_callback,
        )

    for ok, label, msg in results:
        typer.echo(f"{'SUCCESS' if ok else 'FAIL'} {label}: {msg}")

    failed = sum(1 for ok, _, _ in results if not ok)
    if failed:
        typer.echo(f"{failed} download(s) failed.", err=True)
        raise typer.Exit(code=1)

    typer.echo("Git download complete!")


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
        "manual",
        help="Annotation mode: manual|assist|auto|full-auto",
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
    if mode not in ["manual", "assist", "auto", "full-auto"]:
        typer.echo(f"Error: Invalid mode '{mode}'.", err=True)
        raise typer.Exit(code=1)

    if mode == "manual" and (ai_provider or ai_model):
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

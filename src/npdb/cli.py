from pathlib import Path
from dotenv import load_dotenv
import os
import asyncio
import csv
import tempfile
import typer
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.progress import BarColumn, Progress, TextColumn, DownloadColumn, SpinnerColumn
from rich.live import Live
from rich.panel import Panel
from typing import Optional
import httpx

from npdb.managers import (
    DataNeuroPolyMTL,
    BagelNeuroPolyMTL,
    BIDSStandardizer,
    PreflightError,
)
from npdb.managers.neurobagel import NeurobagelAnnotator
from npdb.external.neurobagel.errors import BagelCLIError, classify_bagel_error
from npdb.ledger.ledger import (
    RunLedger,
    success_entry_from_report,
    minimal_success_entry,
    failure_entry,
    generic_failure_entry,
    PROBLEM_GIT_CLONE_FAILURE,
    PROBLEM_MISSING_PARTICIPANTS_TSV,
    PROBLEM_DESCRIPTION_EXTENSION_FAILURE,
    PROBLEM_PREFLIGHT_FAILURE,
    PROBLEM_ANNOTATION_FAILURE,
)
from npdb.ui.display import CommandDisplay, capture_stdout
from npdb.annotation import AnnotationConfig
from npdb.annotation.standardize import load_header_map, validate_header_map_keys
from npdb.automation.mappings.solvers import load_static_mappings


OPTION_GROUP_NAMES = {
    "input": "Input Options",
    "output": "Output Options",
    "behavior": "Behavior Options",
    "automation": "Automation Options",
    "ai": "AI Options",
    "troubleshooting": "Troubleshooting",
}


def show_help(ctx: typer.Context, value: bool):
    """Callback to display the command help and exit."""
    if value:
        typer.echo(ctx.get_help())
        raise typer.Exit()


def help_option():
    """Create a reusable help option for commands."""
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
    """NeuroPoly Database annotation automation and conversion toolkit."""
    return

# ── gitea2bagel command ──────────────────────────────────────────────


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
    ledger: Optional[Path] = typer.Option(
        None,
        "--ledger",
        help="Path to run ledger JSON (default: <output>/run_ledger.json).",
        rich_help_panel=OPTION_GROUP_NAMES["output"],
    ),
    cache_dir: Optional[Path] = typer.Option(
        None,
        "--cache-dir",
        help="Directory for persistent git clones. Reuses existing clones via fetch.",
        file_okay=False,
        dir_okay=True,
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    extend_modalities: bool = typer.Option(
        True,
        "--extend-modalities/--no-extend-modalities",
        help=(
            "When set, automatically map unsupported BIDS imaging suffixes to "
            "Neurobagel Image IRIs (using built-in heuristics or an LLM) and "
            "retry the conversion rather than failing immediately."
        ),
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    fallback_description: bool = typer.Option(
        True,
        "--fallback-description/--no-fallback-description",
        help=(
            "When dataset_description.json is missing, write a minimal BIDS-compliant "
            "fallback (Name + BIDSVersion 1.7.0) and retry rather than aborting."
        ),
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    validate_schema: bool = typer.Option(
        True,
        "--validate-schema/--no-validate-schema",
        help=(
            "Validate phenotypes_annotations.json against the Neurobagel data dictionary "
            "schema before calling 'bagel pheno'. Raises an actionable error on failure."
        ),
        rich_help_panel=OPTION_GROUP_NAMES["behavior"],
    ),
    help_: bool = help_option(),
):
    """
    [bold]Convert a BIDS dataset from Gitea to Neurobagel JSON-LD format[/bold]

    This command automates annotation of phenotypic data using the selected mode:
    • [cyan]manual[/cyan]: Interactive annotation tool
    • [cyan]assist[/cyan]: Browser automation with user confirmation
    • [cyan]auto[/cyan]: Fully automated with ML-based suggestions
    • [cyan]full-auto[/cyan]: Experimental unattended mode (requires review!)
    """
    # Resolve ledger path
    ledger_path = ledger if ledger is not None else output / "run_ledger.json"
    run_ledger = RunLedger(ledger_path)

    display = CommandDisplay()
    with Live(display, refresh_per_second=4, transient=False) as live:
        try:
            load_dotenv(os.path.join(
                os.path.dirname(__file__), "..", "..", ".env"))
        except Exception as e:
            live.console.print(f"Error loading .env file: {e}")

        # Validate annotation options
        if mode not in ["manual", "assist", "auto", "full-auto"]:
            live.console.print(f"[red]Error: Invalid mode '{mode}'.[/red]")
            raise typer.Exit(code=1)

        if mode == "manual" and (ai_provider or ai_model):
            live.console.print(
                "[yellow]Warning: AI options ignored in manual mode.[/yellow]")

        if ai_provider and not ai_model:
            live.console.print(
                "[red]Error: --ai-model required with --ai-provider.[/red]")
            raise typer.Exit(code=1)

        if ai_model and not ai_provider:
            live.console.print(
                "[red]Error: --ai-provider required with --ai-model.[/red]")
            raise typer.Exit(code=1)

        # Validate header map keys against phenotype_mappings
        if header_map:
            try:
                hmap = load_header_map(header_map)
                static = load_static_mappings()
                valid_keys = set(static.get("mappings", {}).keys())
                validate_header_map_keys(hmap, valid_keys)
            except (ValueError, FileNotFoundError) as e:
                live.console.print(f"[red]Error: {e}[/red]")
                raise typer.Exit(code=1)

        display.start_step("Initializing Gitea manager")
        gitea_manager = DataNeuroPolyMTL(
            os.environ.get("NP_GITEA_APP_URL"),
            os.environ.get("NP_GITEA_APP_USER"),
            os.environ.get("NP_GITEA_APP_TOKEN"),
            ssl_verify=verify_ssl
        )
        display.complete_step()

        display.start_step("Cloning dataset repository from Gitea")
        with tempfile.TemporaryDirectory() as local_clone:
            try:
                gitea_manager.clone_repository(
                    dataset,
                    local_clone,
                    light=True,
                    cache_dir=str(cache_dir) if cache_dir else None,
                    output_callback=display.append_output,
                )
            except RuntimeError as clone_err:
                display.fail_step()
                live.console.print(
                    f"[red]Error cloning repository: {clone_err}[/red]")
                run_ledger.append(generic_failure_entry(
                    dataset=dataset,
                    process="gitea2bagel",
                    problem_name=PROBLEM_GIT_CLONE_FAILURE,
                    problem_description=str(clone_err),
                    fix_steps=[
                        {
                            "action": "Verify Gitea credentials and network connectivity",
                            "detail": "Check that NP_GITEA_APP_URL, NP_GITEA_APP_USER and "
                                      "NP_GITEA_APP_TOKEN are set correctly in your .env file "
                                      "and that the Gitea server is reachable.",
                            "auto_fixable": False,
                        },
                        {
                            "action": "Confirm the dataset name is correct",
                            "detail": f"Attempted to clone: {dataset}. "
                                      "Verify it is listed under the 'datasets' organisation on Gitea.",
                            "auto_fixable": False,
                        },
                    ],
                    raw_snippet=str(clone_err)[:500],
                ))
                raise typer.Exit(code=1)
            display.complete_step()

            display.start_step("Extending dataset description")
            try:
                dataset_description = gitea_manager.extend_description(
                    dataset, local_clone)
            except FileNotFoundError as desc_err:
                if fallback_description:
                    _desc_fallback = {"Name": dataset, "BIDSVersion": "1.7.0"}
                    _desc_file = os.path.join(
                        local_clone, "dataset_description.json")
                    import json as _json
                    with open(_desc_file, "w", encoding="utf-8") as _fh:
                        _json.dump(_desc_fallback, _fh, indent=2)
                    live.console.print(
                        "[yellow]dataset_description.json not found; "
                        "wrote minimal fallback (Name + BIDSVersion 1.7.0) and retrying.[/yellow]"
                    )
                    try:
                        dataset_description = gitea_manager.extend_description(
                            dataset, local_clone)
                    except Exception as retry_err:
                        display.fail_step()
                        live.console.print(
                            f"[red]Error extending dataset description after fallback: {retry_err}[/red]")
                        run_ledger.append(generic_failure_entry(
                            dataset=dataset,
                            process="gitea2bagel",
                            problem_name=PROBLEM_DESCRIPTION_EXTENSION_FAILURE,
                            problem_description=str(retry_err),
                            fix_steps=[
                                {
                                    "action": "Check dataset_description.json exists and is valid JSON",
                                    "detail": "The file must be present at the repository root. "
                                              "Validate it with: uv run python -m json.tool dataset_description.json",
                                    "auto_fixable": False,
                                },
                            ],
                            raw_snippet=str(retry_err)[:500],
                        ))
                        raise typer.Exit(code=1)
                else:
                    display.fail_step()
                    live.console.print(
                        f"[red]Error extending dataset description: {desc_err}[/red]")
                    run_ledger.append(generic_failure_entry(
                        dataset=dataset,
                        process="gitea2bagel",
                        problem_name=PROBLEM_DESCRIPTION_EXTENSION_FAILURE,
                        problem_description=str(desc_err),
                        fix_steps=[
                            {
                                "action": "Check dataset_description.json exists and is valid JSON",
                                "detail": "The file must be present at the repository root. "
                                          "Validate it with: uv run python -m json.tool dataset_description.json",
                                "auto_fixable": False,
                            },
                            {
                                "action": "Enable the minimal-description fallback",
                                "detail": "Re-run without --no-fallback-description so npdb writes a "
                                          "minimal BIDS-compliant dataset_description.json automatically.",
                                "auto_fixable": True,
                            },
                        ],
                        raw_snippet=str(desc_err)[:500],
                    ))
                    raise typer.Exit(code=1)
            except Exception as desc_err:
                display.fail_step()
                live.console.print(
                    f"[red]Error extending dataset description: {desc_err}[/red]")
                run_ledger.append(generic_failure_entry(
                    dataset=dataset,
                    process="gitea2bagel",
                    problem_name=PROBLEM_DESCRIPTION_EXTENSION_FAILURE,
                    problem_description=str(desc_err),
                    fix_steps=[
                        {
                            "action": "Check dataset_description.json exists and is valid JSON",
                            "detail": "The file must be present at the repository root. "
                                      "Validate it with: uv run python -m json.tool dataset_description.json",
                            "auto_fixable": False,
                        },
                        {
                            "action": "Verify BIDS dataset structure",
                            "detail": "The cloned directory must follow the BIDS layout. "
                                      "dataset_description.json is required at the root level per the BIDS spec.",
                            "auto_fixable": False,
                        },
                    ],
                    raw_snippet=str(desc_err)[:500],
                ))
                raise typer.Exit(code=1)
            display.complete_step()

            # Annotation step: use selected mode
            participants_tsv = os.path.join(local_clone, "participants.tsv")

            if not os.path.exists(participants_tsv):
                live.console.print(
                    "[red]Error: participants.tsv not found in dataset.[/red]")
                run_ledger.append(generic_failure_entry(
                    dataset=dataset,
                    process="gitea2bagel",
                    problem_name=PROBLEM_MISSING_PARTICIPANTS_TSV,
                    problem_description="participants.tsv was not found in the cloned dataset.",
                    fix_steps=[
                        {
                            "action": "Verify the dataset is a valid BIDS dataset",
                            "detail": "A BIDS dataset must contain participants.tsv at its root. "
                                      "If the file is missing the dataset may be incomplete or "
                                      "not structured to the BIDS specification.",
                            "auto_fixable": False,
                        },
                        {
                            "action": "Create a minimal participants.tsv",
                            "detail": "At minimum the file needs a 'participant_id' column. "
                                      "You can generate one from sub-* directories with:\n"
                                      "  ls -d sub-* | sed 's/^//' > participants.tsv",
                            "auto_fixable": False,
                        },
                    ],
                ))
                raise typer.Exit(code=1)

            # Emit warning for full-auto mode
            if mode == "full-auto":
                live.console.print(
                    "[yellow]\n⚠️  WARNING: EXPERIMENTAL/UNSTABLE MODE\n"
                    "Full-auto annotation uses AI and browser automation without validation.\n"
                    "Review run_ledger.json before using annotations.\n[/yellow]"
                )

            # Pre-validate output and artifacts directories
            try:
                output.mkdir(parents=True, exist_ok=True)
                if not output.is_dir() or not os.access(output, os.W_OK):
                    live.console.print(
                        f"[red]Error: Output directory '{output}' is not writable.[/red]")
                    raise typer.Exit(code=1)
            except OSError as e:
                live.console.print(
                    f"[red]Error: Cannot create/access output directory '{output}': {e}[/red]")
                raise typer.Exit(code=1)

            if artifacts_dir:
                try:
                    artifacts_dir.mkdir(parents=True, exist_ok=True)
                    if not artifacts_dir.is_dir() or not os.access(artifacts_dir, os.W_OK):
                        live.console.print(
                            f"[red]Error: Artifacts directory '{artifacts_dir}' is not writable.[/red]")
                        raise typer.Exit(code=1)
                except OSError as e:
                    live.console.print(
                        f"[red]Error: Cannot create/access artifacts directory '{artifacts_dir}': {e}[/red]")
                    raise typer.Exit(code=1)

            display.start_step(f"Running annotation ({mode} mode)")
            annotation_report = None
            try:
                annotation_config = AnnotationConfig(
                    mode=mode,
                    headless=headless,
                    timeout=timeout,
                    artifacts_dir=artifacts_dir,
                    ai_provider=ai_provider,
                    ai_model=ai_model,
                    phenotype_dictionary=phenotype_dict,
                    header_map=header_map,
                )

                annotation_manager = NeurobagelAnnotator(annotation_config)

                # Execute annotation automation based on mode.
                # capture_stdout feeds print() lines from automation code into
                # the step box instead of letting them escape the Live display.
                with capture_stdout(display.append_output):
                    success, annotation_report = asyncio.run(annotation_manager.execute(
                        participants_tsv_path=Path(participants_tsv),
                        output_dir=output,
                        dataset_name=dataset,
                    ))

                if not success:
                    display.fail_step()
                    live.console.print(
                        f"[yellow]⚠️  Annotation mode '{mode}' execution failed.[/yellow]"
                    )
                    if mode == "manual":
                        typer.prompt(
                            "Press Enter once you have saved the phenotypes files to continue...")
                else:
                    display.complete_step()

            except Exception as e:
                display.fail_step()
                live.console.print(f"[red]Error during annotation: {e}[/red]")
                live.console.print(
                    "[yellow]Falling back to manual annotation.[/yellow]")
                run_ledger.append(generic_failure_entry(
                    dataset=dataset,
                    process="gitea2bagel",
                    problem_name=PROBLEM_ANNOTATION_FAILURE,
                    problem_description=str(e),
                    fix_steps=[
                        {
                            "action": "Review the annotation tool output",
                            "detail": "Check the step output displayed above for browser automation "
                                      "errors, timeouts, or unexpected UI states.",
                            "auto_fixable": False,
                        },
                        {
                            "action": "Re-run in manual mode",
                            "detail": "Use --mode manual to skip automation and annotate the "
                                      "phenotypes interactively via the Neurobagel annotation tool.",
                            "auto_fixable": False,
                        },
                    ],
                    raw_snippet=str(e)[:500],
                ))
                typer.prompt(
                    "Press Enter once you have saved the phenotypes files to continue...")

            display.start_step("Converting to JSON-LD")
            bagel_manager = BagelNeuroPolyMTL(output.absolute().as_posix())
            run_warnings: dict = {}

            try:
                bagel_manager.convert_bids(
                    dataset=dataset,
                    bids_dir=local_clone,
                    phenotypes_tsv=os.path.join(
                        output, f"{dataset}_phenotypes.tsv"),
                    phenotypes_annotations=os.path.join(
                        output, f"{dataset}_phenotypes_annotations.json"),
                    dataset_description=dataset_description,
                    warnings_out=run_warnings,
                    extend_modalities=extend_modalities,
                    validate_schema=validate_schema,
                )
                display.complete_step()

                preproc_warn = run_warnings.get("preprocessing_warnings") or []
                subj_warn = run_warnings.get(
                    "subject_alignment_warnings") or []
                vocab_ext_pending = run_warnings.get(
                    "vocab_extension_pending") or []

                # Ledger: success entry
                if annotation_report is not None:
                    run_ledger.append(success_entry_from_report(
                        dataset=dataset,
                        process="gitea2bagel",
                        report=annotation_report,
                        preprocessing_warnings=preproc_warn or None,
                        subject_alignment_warnings=subj_warn or None,
                        vocab_extension_pending=vocab_ext_pending or None,
                    ))
                else:
                    run_ledger.append(minimal_success_entry(
                        dataset=dataset,
                        process="gitea2bagel",
                        mode=mode,
                        preprocessing_warnings=preproc_warn or None,
                        subject_alignment_warnings=subj_warn or None,
                        vocab_extension_pending=vocab_ext_pending or None,
                    ))

                live.console.print(
                    f"[green]✅ Conversion complete! Output saved to: {output}[/green]"
                )

            except PreflightError as pf_err:
                display.fail_step()
                live.console.print(
                    f"[red]Pre-flight check failed: {pf_err.description}[/red]"
                )
                preproc_warn = run_warnings.get("preprocessing_warnings") or []
                run_ledger.append(generic_failure_entry(
                    dataset=dataset,
                    process="gitea2bagel",
                    problem_name=PROBLEM_PREFLIGHT_FAILURE,
                    problem_description=pf_err.description,
                    fix_steps=pf_err.fix_steps,
                    raw_snippet=pf_err.raw_snippet,
                    preprocessing_warnings=preproc_warn or None,
                ))
                raise typer.Exit(code=1)

            except BagelCLIError as err:
                display.fail_step()

                # Re-render clean Bagel output
                live.console.print(err.rich_output)

                # Classify and show actionable guidance
                classified = classify_bagel_error(err.plain_output)
                if classified:
                    for match in classified:
                        step_lines = []
                        for i, s in enumerate(match["fix_steps"]):
                            if isinstance(s, dict):
                                action = s.get("action", "")
                                detail = s.get("detail", "")
                                auto = " [green](auto-fixable)[/green]" if s.get(
                                    "auto_fixable") else ""
                                step_lines.append(f"  {i + 1}. {action}{auto}")
                                if detail:
                                    for line in detail.splitlines():
                                        step_lines.append(
                                            f"     [dim]{line}[/dim]")
                            else:
                                step_lines.append(f"  {i + 1}. {s}")
                        steps_text = "\n".join(step_lines)
                        live.console.print(Panel(
                            f"{match['description']}\n\n[bold]Steps to fix:[/bold]\n{steps_text}",
                            title=f"[red]{match['problem']}[/red]",
                            border_style="red",
                        ))
                else:
                    live.console.print(Panel(
                        "Bagel failed with an unrecognised error.\n"
                        "Review the output above for details.",
                        title="[red]Bagel CLI Error[/red]",
                        border_style="red",
                    ))

                preproc_warn = run_warnings.get("preprocessing_warnings") or []
                subj_warn = run_warnings.get(
                    "subject_alignment_warnings") or []

                # Ledger: failure entry
                run_ledger.append(failure_entry(
                    dataset=dataset,
                    process="gitea2bagel",
                    err=err,
                    classified=classified,
                    preprocessing_warnings=preproc_warn or None,
                    subject_alignment_warnings=subj_warn or None,
                ))

                raise typer.Exit(code=1)

# ── download command ──────────────────────────────────────────────


def _read_download_tsv(tsv_path: Path) -> list[dict]:
    """Parse a query-results TSV into a list of row dicts."""
    with open(tsv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError("TSV file is empty or has no header row")
        rows = list(reader)
    if not rows:
        raise ValueError("TSV file contains no data rows")
    return rows


def _fetch_url(url: str, dest: Path, timeout: int = 300) -> tuple[bool, str]:
    """Download *url* to *dest* using httpx (streaming)."""
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
    help_: bool = help_option(),
):
    """
    [bold]Download imaging data from query results TSV[/bold]

    This command reads a TSV file containing query results and downloads the
    associated imaging data using one of three modes:

    • [cyan]URL mode (default):[/cyan] Reads the [bold]AccessLink[/bold] column and downloads
      each URL in parallel using HTTP.
    • [cyan]Git mode[/cyan] ([bold]--git[/bold]): Performs an authenticated shallow sparse-checkout
      clone from the [bold]RepositoryURL[/bold] column, limiting the working tree to the
      [bold]ImagingSessionPath[/bold] for each row.  Requires [bold]NP_GITEA_APP_URL[/bold],
      [bold]NP_GITEA_APP_USER[/bold] and [bold]NP_GITEA_APP_TOKEN[/bold] environment variables.
    • [cyan]Git-annex mode[/cyan] ([bold]--git[/bold] [bold]--git-annex[/bold]): Same as git mode, but
      also runs [bold]git annex get[/bold] after cloning to fetch file content from
      annex pointers.
    """
    if git_annex and not git:
        typer.echo(
            "Error: --git-annex requires --git.", err=True)
        raise typer.Exit(code=1)

    # Parse TSV
    try:
        rows = _read_download_tsv(query_results)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error reading TSV: {exc}", err=True)
        raise typer.Exit(code=1)

    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Mode 1: direct URL downloads ──────────────────────────────
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
            # Derive filename from the URL's last path segment
            filename = os.path.basename(url.split("?")[0]) or f"{subject}.bin"
            dest = output_dir / dataset / subject / filename
            jobs.append((url, dest, dataset, subject))

        if not jobs:
            typer.echo(
                "Warning: No valid AccessLink URLs found in TSV.", err=True)
            return

        typer.echo(
            f"Downloading {len(jobs)} file(s) ({max_workers} workers)...")
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
                        f"{'✓' if ok else '✗'} {dataset}/{subject}: {msg}")
                    progress.advance(task)

        typer.echo("✅ Download complete!")
        return

    # ── Mode 2 / 3: git (+ optional git-annex) ────────────────────
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

    gitea_url = os.environ.get("NP_GITEA_APP_URL")
    gitea_user = os.environ.get("NP_GITEA_APP_USER")
    gitea_token = os.environ.get("NP_GITEA_APP_TOKEN")

    if not all([gitea_url, gitea_user, gitea_token]):
        typer.echo(
            "Error: NP_GITEA_APP_URL, NP_GITEA_APP_USER and NP_GITEA_APP_TOKEN "
            "must be set for git mode.",
            err=True,
        )
        raise typer.Exit(code=1)

    gitea_manager = DataNeuroPolyMTL(
        gitea_url, gitea_user, gitea_token, ssl_verify=verify_ssl
    )

    # Build subject list from TSV (ImagingSession rows only)
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

    results = gitea_manager.download_subjects(
        subjects, output_dir, use_annex=git_annex)

    for ok, label, msg in results:
        typer.echo(f"{'✓' if ok else '✗'} {label}: {msg}")

    failed = sum(1 for ok, _, _ in results if not ok)
    if failed:
        typer.echo(f"⚠️  {failed} download(s) failed.", err=True)
        raise typer.Exit(code=1)

    typer.echo("✅ Git download complete!")


# ── standardize subgroup ──────────────────────────────────────────


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
    # Validate mode
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

    # Validate BIDS root
    participants_tsv = bids_dir / "participants.tsv"
    if not participants_tsv.exists():
        typer.echo(
            f"Error: participants.tsv not found in {bids_dir}.", err=True
        )
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo("Dry-run mode: no files will be modified.\n")

    try:
        config = AnnotationConfig(
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

        standardizer = BIDSStandardizer(config)
        success = asyncio.run(standardizer.execute(input_path=bids_dir))

        if success:
            if dry_run:
                typer.echo("\nDry-run complete. No files were modified.")
            else:
                typer.echo(
                    f"\n✅ BIDS standardization complete: {bids_dir}"
                )
        else:
            typer.echo("⚠️  Standardization completed with warnings.", err=True)
            raise typer.Exit(code=1)

    except Exception as e:
        typer.echo(f"Error during BIDS standardization: {e}", err=True)
        raise typer.Exit(code=1)

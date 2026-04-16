from pathlib import Path
from dotenv import load_dotenv
import os
import asyncio
import tempfile
import typer
from rich.progress import Progress, SpinnerColumn, TextColumn
from typing import Optional

from npdb.managers import (
    DataNeuroPolyMTL,
    BagelNeuroPolyMTL,
    BIDSStandardizer,
)
from npdb.managers.neurobagel import NeurobagelAnnotator
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
    • [cyan]manual[/cyan]: Interactive annotation tool
    • [cyan]assist[/cyan]: Browser automation with user confirmation
    • [cyan]auto[/cyan]: Fully automated with ML-based suggestions
    • [cyan]full-auto[/cyan]: Experimental unattended mode (requires review!)
    """
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        try:
            load_dotenv(os.path.join(
                os.path.dirname(__file__), "..", "..", ".env"))
        except Exception as e:
            typer.echo(f"Error loading .env file: {e}")

        # Validate annotation options
        if mode not in ["manual", "assist", "auto", "full-auto"]:
            typer.echo(
                f"Error: Invalid mode '{mode}'.", err=True)
            raise typer.Exit(code=1)

        if mode == "manual" and (ai_provider or ai_model):
            typer.echo("Warning: AI options ignored in manual mode.", err=True)

        if ai_provider and not ai_model:
            typer.echo(
                "Error: --ai-model required with --ai-provider.", err=True)
            raise typer.Exit(code=1)

        if ai_model and not ai_provider:
            typer.echo(
                "Error: --ai-provider required with --ai-model.", err=True)
            raise typer.Exit(code=1)

        # Validate header map keys against phenotype_mappings
        if header_map:
            try:
                hmap = load_header_map(header_map)
                static = load_static_mappings()
                valid_keys = set(static.get("mappings", {}).keys())
                validate_header_map_keys(hmap, valid_keys)
            except (ValueError, FileNotFoundError) as e:
                typer.echo(f"Error: {e}", err=True)
                raise typer.Exit(code=1)

        task = progress.add_task("Initializing Gitea manager...", total=None)
        gitea_manager = DataNeuroPolyMTL(
            os.environ.get("NP_GITEA_APP_URL"),
            os.environ.get("NP_GITEA_APP_USER"),
            os.environ.get("NP_GITEA_APP_TOKEN"),
            ssl_verify=verify_ssl
        )
        progress.remove_task(task)

        task = progress.add_task(
            "Surface cloning dataset repository from Gitea...", total=None)
        with tempfile.TemporaryDirectory() as local_clone:
            gitea_manager.clone_repository(dataset, local_clone, light=True)
            progress.remove_task(task)

            task = progress.add_task(
                "Extending dataset description...", total=None)
            dataset_description = gitea_manager.extend_description(
                dataset, local_clone)
            progress.remove_task(task)

            # Annotation step: use selected mode
            participants_tsv = os.path.join(local_clone, "participants.tsv")

            if not os.path.exists(participants_tsv):
                typer.echo(
                    f"Error: participants.tsv not found in dataset.", err=True)
                raise typer.Exit(code=1)

            # Emit warning for full-auto mode
            if mode == "full-auto":
                typer.echo(
                    "\n⚠️  WARNING: EXPERIMENTAL/UNSTABLE MODE\n"
                    "Full-auto annotation uses AI and browser automation without validation.\n"
                    "Review phenotypes_provenance.json before using annotations.\n",
                    err=True
                )

            # Pre-validate output and artifacts directories
            try:
                output.mkdir(parents=True, exist_ok=True)
                if not output.is_dir() or not os.access(output, os.W_OK):
                    typer.echo(
                        f"Error: Output directory '{output}' is not writable.", err=True)
                    raise typer.Exit(code=1)
            except OSError as e:
                typer.echo(
                    f"Error: Cannot create/access output directory '{output}': {e}", err=True)
                raise typer.Exit(code=1)

            if artifacts_dir:
                try:
                    artifacts_dir.mkdir(parents=True, exist_ok=True)
                    if not artifacts_dir.is_dir() or not os.access(artifacts_dir, os.W_OK):
                        typer.echo(
                            f"Error: Artifacts directory '{artifacts_dir}' is not writable.", err=True)
                        raise typer.Exit(code=1)
                except OSError as e:
                    typer.echo(
                        f"Error: Cannot create/access artifacts directory '{artifacts_dir}': {e}", err=True)
                    raise typer.Exit(code=1)

            task = progress.add_task(
                f"Running annotation ({mode} mode)...", total=None)

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

                # Execute annotation automation based on mode
                success = asyncio.run(annotation_manager.execute(
                    participants_tsv_path=Path(participants_tsv),
                    output_dir=output
                ))

                if not success:
                    typer.echo(
                        f"⚠️  Annotation mode '{mode}' execution failed.",
                        err=True
                    )
                    if mode == "manual":
                        typer.prompt(
                            "Press Enter once you have saved the phenotypes files to continue...")

            except Exception as e:
                typer.echo(f"Error during annotation: {e}", err=True)
                typer.echo("Falling back to manual annotation.", err=True)
                typer.prompt(
                    "Press Enter once you have saved the phenotypes files to continue...")
            progress.remove_task(task)

            task = progress.add_task("Converting to JSON-LD...", total=None)
            bagel_manager = BagelNeuroPolyMTL(output.absolute().as_posix())

            bagel_manager.convert_bids(
                dataset=dataset,
                bids_dir=local_clone,
                phenotypes_tsv=os.path.join(output, "phenotypes.tsv"),
                phenotypes_annotations=os.path.join(
                    output, "phenotypes_annotations.json"),
                dataset_description=dataset_description
            )
            progress.remove_task(task)

            typer.echo(
                f"✅ Conversion complete! Output saved to: {output}"
            )


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

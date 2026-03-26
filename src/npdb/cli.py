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
    AnnotationManager,
    AnnotationConfig,
)


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

        progress.add_task("Initializing Gitea manager...", total=None)
        gitea_manager = DataNeuroPolyMTL(
            os.environ.get("NP_GITEA_APP_URL"),
            os.environ.get("NP_GITEA_APP_USER"),
            os.environ.get("NP_GITEA_APP_TOKEN"),
            ssl_verify=verify_ssl
        )

        progress.add_task(
            "Surface cloning dataset repository from Gitea...", total=None)
        with tempfile.TemporaryDirectory() as local_clone:
            gitea_manager.clone_repository(dataset, local_clone, light=True)

            progress.add_task("Extending dataset description...", total=None)
            dataset_description = gitea_manager.extend_description(
                dataset, local_clone)

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

            progress.add_task(
                f"Running annotation ({mode} mode)...", total=None)

            try:
                annotation_config = AnnotationConfig(
                    mode=mode,
                    headless=headless,
                    timeout=timeout,
                    artifacts_dir=artifacts_dir,
                    ai_provider=ai_provider,
                    ai_model=ai_model,
                    phenotype_dictionary=phenotype_dict
                )

                annotation_manager = AnnotationManager(annotation_config)

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

            progress.add_task("Converting to JSON-LD...", total=None)
            bagel_manager = BagelNeuroPolyMTL(output.absolute().as_posix())

            bagel_manager.convert_bids(
                dataset=dataset,
                bids_dir=local_clone,
                phenotypes_tsv=os.path.join(output, "phenotypes.tsv"),
                phenotypes_annotations=os.path.join(
                    output, "phenotypes_annotations.json"),
                dataset_description=dataset_description
            )

            typer.echo(
                f"✅ Conversion complete! Output saved to: {output}"
            )


@npdb.command()
def hot_reload(
    jsonld: Path = typer.Argument(
        ...,
        help="Path to JSON-LD file to upload to the graph",
        exists=True,
        dir_okay=False,
    ),
    dataset_uuid: str = typer.Argument(
        ...,
        help="Unique identifier for the dataset (e.g., 'my-dataset-001')",
    ),
    dataset_name: str = typer.Option(
        ...,
        "--name",
        help="Dataset name (appears in UI)",
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    graph_host: str = typer.Option(
        "graph",
        help="GraphDB hostname (devcontainer: 'graph', standalone: 'graphdb')",
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    graph_port: int = typer.Option(
        7200,
        help="GraphDB HTTP port",
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    graph_db: str = typer.Option(
        "repositories/my_db",
        help="GraphDB repository path",
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    username: Optional[str] = typer.Option(
        None,
        help="GraphDB username (env var: NB_GRAPH_USERNAME)",
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    password: Optional[str] = typer.Option(
        None,
        help="GraphDB password (env var: NB_GRAPH_PASSWORD)",
        rich_help_panel=OPTION_GROUP_NAMES["input"],
    ),
    metadata_file: Optional[Path] = typer.Option(
        None,
        help="Path to datasets_metadata.json to update",
        file_okay=True,
        dir_okay=False,
        writable=True,
        rich_help_panel=OPTION_GROUP_NAMES["output"],
    ),
    help_: bool = help_option(),
):
    """
    [bold]Hot-reload a dataset to a running Neurobagel node without restart[/bold]

    This command uploads a JSON-LD file directly to the GraphDB instance,
    making the dataset immediately queryable without container restart.

    [cyan]Example:[/cyan]
    npdb hot-reload whole-spine.jsonld my-dataset-001 \\
      --name "My Research Dataset" \\
      --graph-host graphdb \\
      --metadata-file /data/datasets_metadata.json
    """
    try:
        load_dotenv(os.path.join(
            os.path.dirname(__file__), "..", "..", ".env"))
    except Exception as e:
        typer.echo(f"Warning: Could not load .env file: {e}")

    # Get credentials from environment if not provided
    if not username:
        username = os.environ.get("NB_GRAPH_USERNAME", "DBUSER")
    if not password:
        password = os.environ.get("NB_GRAPH_PASSWORD")

    if not password:
        typer.echo(
            "Error: GraphDB password required. Set via --password or NB_GRAPH_PASSWORD env var.",
            err=True
        )
        raise typer.Exit(code=1)

    try:
        from npdb.managers.graph_updater import GraphUpdater

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            progress.add_task(
                f"🚀 Hot-reloading {jsonld.name}...", total=None)

            updater = GraphUpdater(
                graph_host=graph_host,
                graph_port=graph_port,
                graph_db=graph_db,
                username=username,
                password=password,
            )

            # Prepare metadata
            dataset_metadata = {
                "dataset_name": dataset_name,
            }

            # Perform hot-reload
            success = updater.hot_reload_dataset(
                jsonld_path=jsonld,
                dataset_uuid=dataset_uuid,
                dataset_metadata=dataset_metadata,
                datasets_metadata_path=metadata_file,
            )

            if success:
                typer.echo(
                    f"✅ Dataset '{dataset_name}' is now live in the Neurobagel node!"
                )
                typer.echo(
                    f"   Dataset UUID: {dataset_uuid}")
                typer.echo(
                    f"   JSON-LD: {jsonld}")
                if metadata_file:
                    typer.echo(
                        f"   Metadata: {metadata_file}")
                else:
                    typer.echo(
                        "⚠️  Note: Metadata file not provided. Dataset is in GraphDB but may not appear in API/UI.")
                    typer.echo(
                        "   To fix: save datasets_metadata.json inside the container at /data/datasets_metadata.json")
            else:
                typer.echo(
                    "❌ Hot-reload failed. Check logs above for details.", err=True)
                raise typer.Exit(code=1)

    except Exception as e:
        typer.echo(f"Error during hot-reload: {e}", err=True)
        raise typer.Exit(code=1)

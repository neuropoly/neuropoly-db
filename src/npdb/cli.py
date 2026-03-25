from pathlib import Path
from dotenv import load_dotenv
import os
import tempfile
import typer
from rich.progress import Progress, SpinnerColumn, TextColumn
import webbrowser

from npdb.managers import DataNeuroPolyMTL, BagelNeuroPolyMTL


npdb = typer.Typer(
    help="Conversion tools and utilities for NeuroPoly Database (BIDS)",
    context_settings={"help_option_names": ["--help", "-h"]},
    no_args_is_help=True,
    rich_markup_mode="rich"
)


def show_help(ctx: typer.Context, value: bool):
    """
    Callback to display the command help and exit.
    Adapted from https://github.com/fastapi/typer/discussions/833#discussioncomment-9551792.
    """
    if value:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@npdb.command()
def gitea2bagel(
    dataset: str = typer.Argument(
        ...,
        help="Name of the dataset on Gitea (under the datasets organization)."
    ),
    output: Path = typer.Argument(
        ...,
        help="Path to the output directory where the converted dataset will be saved.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True
    ),
    verify_ssl: bool = typer.Option(
        True,
        help="Whether to verify SSL certificates when connecting to Gitea."
    )
):
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

            # Prompt user to load participants.tsv in the browser page that opens,
            # follow the annotations instructions, and save the phenotypes tsv and
            # annotations files giving specific paths in the output directory.
            typer.echo("Please load participants.tsv located at :")
            typer.echo(f"  {local_clone}/participants.tsv")
            typer.echo(
                "in the browser and follow the instructions to create the phenotypes annotations file.")
            typer.echo(
                "Once done, save the phenotypes TSV and annotations JSON files in the output directory,")
            typer.echo(f"  - Phenotypes TSV: {output / 'phenotypes.tsv'}")
            typer.echo(
                f"  - Phenotypes annotations: {output / 'phenotypes_annotations.json'}")
            # Wait for 10 seconds to allow the user to read the instructions before opening the browser
            # page and blocking on input, to ensure they see the instructions before the page opens.
            import time
            time.sleep(10)
            webbrowser.open_new_tab("https://annotate.neurobagel.org")
            typer.prompt(
                "Press Enter to continue after saving the phenotypes files...")

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

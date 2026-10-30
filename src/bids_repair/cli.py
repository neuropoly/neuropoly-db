"""Standalone, local-directory BIDS validation and repair command."""

import json
import re
import sys
from contextlib import redirect_stdout
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.text import Text
from typer.core import TyperCommand, TyperGroup

# Use the exception types understood by the installed Typer command runner.
try:
    from typer._click.core import Context
    from typer._click.exceptions import UsageError
except ImportError:
    from click import Context, UsageError

from bids_repair import (
    BidsRepairError,
    BidsSeverity,
    BidsValidationError,
    get_bids_validation_report,
    plan_bids_repair,
    recover_bids_repair,
    validate_and_repair_bids_dataset,
)
from bids_repair.bids_version import resolve_bids_version
from bids_repair.description import read_dataset_description
from bids_repair.options import parse_suffix_aliases
from bids_repair.paths import assert_safe_path
from bids_repair.standardize import standardize_dataset
from bids_repair.tables import load_header_map
from bids_repair.target import acquire_target_schema
from bids_repair.validation import findings_above_threshold

Dataset = Annotated[
    Path,
    typer.Argument(
        metavar="DATASET",
        exists=True,
        file_okay=False,
        resolve_path=True,
        help="Local BIDS-like dataset directory.",
    ),
]
SchemaOption = Annotated[
    Path | None,
    typer.Option(
        "--schema",
        exists=True,
        help="Matching local schema JSON file/directory; overrides automatic fetching.",
        rich_help_panel="Input Options",
    ),
]
VersionOption = Annotated[
    str | None,
    typer.Option(
        "--bids-version",
        help="Target BIDS standard; defaults to dataset BIDSVersion, then asks in a terminal.",
        rich_help_panel="Input Options",
    ),
]


class OutputFormat(str, Enum):
    human = "human"
    json = "json"


FormatOption = Annotated[
    OutputFormat, typer.Option("--format", rich_help_panel="Output Options")
]
console = Console(highlight=False)
error_console = Console(stderr=True, highlight=False)


class RepairCommand(TyperCommand):
    def invoke(self, ctx: Context) -> Any:
        try:
            return super().invoke(ctx)
        except (typer.Exit, typer.Abort):
            raise
        except (BidsRepairError, ValueError, OSError, RuntimeError) as exc:
            if ctx.params.get("output_format") == "json":
                typer.echo(
                    json.dumps(
                        {
                            "error": {
                                "title": (
                                    "BIDS repair failed"
                                    if isinstance(exc, BidsRepairError)
                                    else "BIDS operation failed"
                                ),
                                "message": str(exc),
                                "hint": "For interrupted repairs use bids-repair recover DATASET.",
                            }
                        }
                    )
                )
                raise typer.Exit(1) from exc
            error_console.print(Text(f"bids-repair: {exc}", style="bold red"))
            raise typer.Exit(1) from exc


class RepairGroup(TyperGroup):
    def parse_args(self, ctx: Context, args: list[str]) -> list[str]:
        if args and args[0] == "--":
            args = ["plan", *args[1:]]
        elif args and args[0] not in self.commands and not args[0].startswith("-"):
            args = ["plan", *args]
        return super().parse_args(ctx, args)


app = typer.Typer(
    cls=RepairGroup,
    no_args_is_help=True,
    rich_markup_mode="rich",
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["--help", "-h"]},
    epilog="Run 'bids-repair DATASET --help' or 'bids-repair COMMAND --help' for options.",
)


@app.callback()
def main() -> None:
    """Validate and repair a local BIDS-like directory.

    bids-repair DATASET plans only; add --apply to write safe repairs.
    Use --bids-version VERSION or the dataset's BIDSVersion; otherwise asks.
    The native beta supports its pinned bundled BIDS schema only.
    Use bids-repair DATASET --help for repair options.
    Provider access and source conversion belong to npdb, not this command.
    """


def _interactive() -> bool:
    return sys.stdin.isatty()


def _declared_version(dataset: Path) -> str | None:
    path = dataset / "dataset_description.json"
    assert_safe_path(dataset, path, must_exist=False)

    problem = None
    if not path.exists():
        problem = "dataset_description.json is missing"
    else:
        try:
            description = read_dataset_description(dataset)
        except (ValueError, UnicodeError) as exc:
            problem = f"dataset_description.json is invalid: {exc}"
        else:
            value = description.get("BIDSVersion")
            if isinstance(value, str) and re.fullmatch(
                r"[0-9]+\.[0-9]+\.[0-9]+", value.strip()
            ):
                return value.strip()
            problem = "dataset_description.json has no usable BIDSVersion"

    error_console.print(Text(f"Warning: {problem}.", style="yellow"))
    return None


def _target_version(
    value: str | None,
    schema_path: Path | None,
    dataset: Path,
) -> tuple[str, Path | None]:
    declared = _declared_version(dataset)
    explicit = value is not None
    if value is None:
        value = declared
    if value is None:
        if not _interactive():
            raise UsageError("Supply --bids-version VERSION for unattended use.")
        with redirect_stdout(sys.stderr):
            value = typer.prompt("Target BIDS standard version", type=str, err=True)

    version = resolve_bids_version(value)
    if version is None:
        raise UsageError("A target BIDS version is required.")
    if explicit and declared is not None and version != declared:
        error_console.print(
            Text(
                f"Warning: CLI BIDS version {version} differs from "
                f"dataset_description.json BIDSVersion {declared}; using CLI target "
                f"{version}. The existing declaration will not be overwritten.",
                style="yellow",
            )
        )

    return version, acquire_target_schema(version, schema_path)


def _aliases(values: list[str] | None) -> dict[str, str]:
    try:
        return parse_suffix_aliases(values)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc


def _emit(result: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        typer.echo(json.dumps(result, indent=2, sort_keys=True))
        return

    if "application" in result:
        summary = result["application"]
        gate = summary["severity_gate"]
        console.print(
            Text(
                f"Final severity check (above {gate['threshold']}): "
                f"{'passed' if gate['passed'] else 'failed'}",
                style="green" if gate["passed"] else "red",
            ),
            soft_wrap=True,
        )
        return

    console.print(
        Text(f"BIDS result for {result.get('dataset', 'dataset')}", style="bold"),
        soft_wrap=True,
    )
    if result.get("coverage"):
        console.print(Text(result["coverage"]), soft_wrap=True)
    for operation in result.get("operations", []):
        console.print(
            Text(
                f"  {operation['kind']}: {operation['source']} -> {operation.get('target', '')}"
            ),
            soft_wrap=True,
        )

    findings = result.get("findings", [])
    console.print(f"Findings: {len(findings)}")
    for finding in findings:
        severity = finding.get("severity", "info")
        console.print(
            Text(
                f"  {severity.upper()} "
                f"{finding.get('code', 'UNKNOWN')} [{finding.get('path', '')}]: "
                f"{finding.get('message', '')} {finding.get('resolution', '')}",
                style={"error": "red", "warning": "yellow", "info": "cyan"}.get(
                    severity, ""
                ),
            ),
            soft_wrap=True,
        )

    if result.get("message"):
        console.print(Text(result["message"]), soft_wrap=True)
    if "participants_json" in result:
        console.print_json(data=result)


@app.command("plan", hidden=True, cls=RepairCommand)
def plan(
    dataset: Dataset,
    apply_changes: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Apply safe repairs and revalidate.",
            rich_help_panel="Behavior Options",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            help="Plan only (the default).", rich_help_panel="Behavior Options"
        ),
    ] = False,
    suffix_alias: Annotated[
        list[str] | None,
        typer.Option(
            "--suffix-alias",
            help="Explicit OLD=NEW suffix translation.",
            rich_help_panel="Behavior Options",
        ),
    ] = None,
    bids_severity_threshold: Annotated[
        BidsSeverity,
        typer.Option(
            case_sensitive=False,
            rich_help_panel="Behavior Options",
            help="Highest allowed final severity; findings strictly above this level fail.",
        ),
    ] = BidsSeverity.WARNING,
    bids_version: VersionOption = None,
    schema_path: SchemaOption = None,
    output_format: FormatOption = OutputFormat.human,
) -> None:
    if apply_changes and dry_run:
        raise UsageError("--apply and --dry-run cannot be used together.")

    aliases = _aliases(suffix_alias)
    version, schema_path = _target_version(bids_version, schema_path, dataset)

    if not apply_changes:
        result = plan_bids_repair(
            dataset,
            bids_version=version,
            suffix_aliases=aliases or None,
            target_version=version,
            schema_path=schema_path,
        )
        _emit(result, output_format)
        return

    failed = False
    try:
        report = validate_and_repair_bids_dataset(
            dataset,
            bids_version=version,
            suffix_aliases=aliases or None,
            severity_threshold=bids_severity_threshold.value,
            target_version=version,
            schema_path=schema_path,
        )
    except BidsValidationError as exc:
        report = exc.report
        failed = True

    summary = report["repair"]
    _emit(
        {
            "dataset": str(dataset),
            "target_bids_version": version,
            "coverage": report.get("coverage"),
            "findings": summary["unresolved"],
            "operations": summary["operations"],
            "blocking": not summary["severity_gate"]["passed"],
            "application": summary,
        },
        output_format,
    )
    if failed:
        raise typer.Exit(1)


@app.command(cls=RepairCommand)
def recover(dataset: Dataset, output_format: FormatOption = OutputFormat.human) -> None:
    """Recover an incomplete repair journal; no version or network access needed."""
    _emit(recover_bids_repair(dataset), output_format)


@app.command(cls=RepairCommand)
def validate(
    dataset: Dataset,
    bids_version: VersionOption = None,
    schema_path: SchemaOption = None,
    output_format: FormatOption = OutputFormat.human,
) -> None:
    """Read-only validation. Errors fail; warnings are reported."""
    version, schema_path = _target_version(bids_version, schema_path, dataset)
    report = get_bids_validation_report(
        dataset, target_version=version, schema_path=schema_path
    )

    findings = [
        issue for item in report["datasets"] for issue in item.get("issues", [])
    ]
    failed = bool(findings_above_threshold(report, dataset_root=dataset))

    _emit(
        {
            "dataset": str(dataset),
            "target_bids_version": version,
            "coverage": report.get("coverage"),
            "findings": findings,
            "blocking": failed,
            "validation": report,
        },
        output_format,
    )
    if failed:
        raise typer.Exit(1)


@app.command(cls=RepairCommand)
def standardize(
    dataset: Dataset,
    header_map: Annotated[
        Path | None,
        typer.Option(exists=True, dir_okay=False, rich_help_panel="Input Options"),
    ] = None,
    column_metadata: Annotated[
        Path | None,
        typer.Option(exists=True, dir_okay=False, rich_help_panel="Input Options"),
    ] = None,
    add_column: Annotated[
        list[str] | None,
        typer.Option(
            "--add-column",
            help="Explicit column to add, filled with n/a.",
            rich_help_panel="Behavior Options",
        ),
    ] = None,
    prepare_layout: Annotated[
        bool,
        typer.Option(
            help="Explicitly normalize layout/create participants.",
            rich_help_panel="Behavior Options",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            help="Preview without dataset writes.", rich_help_panel="Behavior Options"
        ),
    ] = False,
    bids_version: VersionOption = None,
    schema_path: SchemaOption = None,
    output_format: FormatOption = OutputFormat.human,
) -> None:
    """Explicitly standardize tables/sidecars, without Neurobagel automation."""
    version, schema_path = _target_version(bids_version, schema_path, dataset)
    result = standardize_dataset(
        dataset,
        header_map=load_header_map(header_map) if header_map else None,
        column_metadata=(
            json.loads(column_metadata.read_text(encoding="utf-8"))
            if column_metadata
            else None
        ),
        add_missing_columns=add_column or [],
        prepare_layout=prepare_layout,
        dry_run=dry_run,
        target_version=version,
        schema_path=schema_path,
    )
    _emit(result, output_format)


def run() -> None:
    app(prog_name="bids-repair")

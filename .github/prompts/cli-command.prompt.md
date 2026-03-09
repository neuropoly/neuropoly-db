---
description: Template for implementing CLI commands using Typer with rich console output and async support.
applyTo:
  - src/neuropoly_db/cli/**/*.py
---

# CLI Command Implementation

You are implementing a CLI command for the NeuroPoly DB neuroimaging search engine.

## Context

- **CLI Framework**: Typer with rich console output
- **Async Support**: Use `asyncio` for async operations
- **Output**: Rich tables, progress bars, and colored messages
- **Error Handling**: User-friendly error messages, exit codes
- **API Client**: Use centralized API client from `neuropoly_db.core.api_client`

## Code Structure

```python
# src/neuropoly_db/cli/commands/<module>.py
import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress
from typing import Optional
import asyncio

from neuropoly_db.core.api_client import APIClient

app = typer.Typer(help="<Module> operations")
console = Console()

@app.command()
def command_name(
    arg: str = typer.Argument(..., help="Argument description"),
    option: Optional[str] = typer.Option(None, "--option", "-o", help="Option description"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose output")
):
    """
    Command description.
    
    Example:
        neuropoly-db <module> command-name <arg> --option value
    """
    try:
        # For async operations, use asyncio.run()
        if asyncio.iscoroutinefunction(operation):
            result = asyncio.run(operation(arg, option))
        else:
            result = operation(arg, option)
        
        # Display result with rich formatting
        display_result(result)
        
        console.print("[green]✓[/green] Operation completed successfully")
    
    except FileNotFoundError as e:
        console.print(f"[red]✗[/red] File not found: {e}", style="red")
        raise typer.Exit(code=1)
    except ValueError as e:
        console.print(f"[red]✗[/red] Invalid value: {e}", style="red")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]✗[/red] Error: {e}", style="red")
        if verbose:
            console.print_exception()
        raise typer.Exit(code=1)
```

## Guidelines

### Typer Best Practices
- Use `typer.Argument()` for required positional arguments
- Use `typer.Option()` for optional flags and named options
- Add help text to all arguments and options
- Use short forms for common options (`-v` for `--verbose`)
- Group related commands with `typer.Typer()` subapps

### Rich Console Output
- Create a shared `Console()` instance at module level
- Use color-coded messages:
  - `[green]✓[/green]` for success
  - `[red]✗[/red]` for errors
  - `[yellow]![/yellow]` for warnings
  - `[blue]i[/blue]` for info
- Use `Table` for tabular data
- Use `Progress` for long-running operations
- Use `console.print_exception()` for verbose error output

### Async Support
- Use `asyncio.run()` to run async functions from sync CLI commands
- Don't make CLI command functions themselves async (Typer doesn't support it)
- Wrap async logic in helper functions

### Error Handling
- Catch specific exceptions and show user-friendly messages
- Use `typer.Exit(code=X)` to set exit codes (0=success, 1=error)
- Add `--verbose` flag to show full stack traces
- Validate inputs before calling expensive operations

### API Client Usage
- Use centralized `APIClient` from `neuropoly_db.core.api_client`
- Handle connection errors gracefully
- Show progress for long-running API calls

## Example: Search Command

```python
# src/neuropoly_db/cli/commands/search.py
import typer
from rich.console import Console
from rich.table import Table
from rich.progress import track
from typing import Optional, Literal
import asyncio

from neuropoly_db.core.api_client import APIClient, APIError

app = typer.Typer(help="Search neuroimaging data")
console = Console()

@app.command()
def search(
    query: str = typer.Argument(..., help="Search query (natural language)"),
    mode: str = typer.Option(
        "hybrid",
        "--mode",
        "-m",
        help="Search mode: hybrid, semantic, or keyword"
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-k",
        help="Number of results to return",
        min=1,
        max=100
    ),
    dataset: Optional[str] = typer.Option(
        None,
        "--dataset",
        "-d",
        help="Filter by dataset (e.g., ds000001)"
    ),
    output_format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format: table, json, or csv"
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output"
    )
):
    """
    Search neuroimaging metadata using text queries.
    
    Supports multiple search modes:
    - hybrid: Combines keyword and semantic search (best results)
    - semantic: Vector similarity using neural embeddings
    - keyword: Traditional full-text search (BM25)
    
    Examples:
        # Basic search
        neuropoly-db search "T1w brain scans"
        
        # Semantic search with limit
        neuropoly-db search "functional MRI motor task" --mode semantic -k 20
        
        # Filter by dataset
        neuropoly-db search "3 Tesla" --dataset ds000001
        
        # Export results to CSV
        neuropoly-db search "diffusion weighted" --format csv > results.csv
    """
    # Validate mode
    if mode not in ["hybrid", "semantic", "keyword"]:
        console.print(
            f"[red]✗[/red] Invalid mode: {mode}. Must be hybrid, semantic, or keyword.",
            style="red"
        )
        raise typer.Exit(code=1)
    
    try:
        # Create API client
        client = APIClient()
        
        # Show search info
        if verbose:
            console.print(f"[blue]i[/blue] Query: {query}")
            console.print(f"[blue]i[/blue] Mode: {mode}")
            console.print(f"[blue]i[/blue] Limit: {limit}")
            if dataset:
                console.print(f"[blue]i[/blue] Dataset filter: {dataset}")
        
        # Execute search
        with console.status(f"[bold blue]Searching..."):
            results = asyncio.run(
                client.search(
                    query=query,
                    mode=mode,
                    k=limit,
                    dataset_filter=dataset
                )
            )
        
        # Display results
        if not results["results"]:
            console.print("[yellow]![/yellow] No results found.", style="yellow")
            raise typer.Exit(code=0)
        
        console.print(
            f"[green]✓[/green] Found {results['total']} results "
            f"in {results['query_time_ms']:.1f}ms"
        )
        
        if output_format == "json":
            import json
            console.print_json(data=results)
        elif output_format == "csv":
            _display_csv(results["results"])
        else:  # table
            _display_table(results["results"], verbose)
    
    except APIError as e:
        console.print(f"[red]✗[/red] API error: {e}", style="red")
        raise typer.Exit(code=1)
    except ConnectionError:
        console.print(
            "[red]✗[/red] Cannot connect to API. Is the server running?",
            style="red"
        )
        console.print("[blue]i[/blue] Try: neuropoly-db status")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]✗[/red] Unexpected error: {e}", style="red")
        if verbose:
            console.print_exception()
        raise typer.Exit(code=1)

def _display_table(results: list[dict], verbose: bool = False):
    """Display search results as a rich table."""
    table = Table(title="Search Results", show_header=True, header_style="bold magenta")
    
    table.add_column("Dataset", style="cyan", no_wrap=True)
    table.add_column("Subject", style="green")
    table.add_column("Suffix", style="yellow")
    table.add_column("Score", justify="right", style="blue")
    
    if verbose:
        table.add_column("Metadata", overflow="fold")
    
    for result in results:
        row = [
            result["dataset"],
            result["subject"],
            result["suffix"],
            f"{result['score']:.3f}"
        ]
        
        if verbose:
            # Show key metadata fields
            meta = result.get("metadata", {})
            meta_str = " | ".join([
                f"{k}: {v}" for k, v in meta.items()
                if k in ["MagneticFieldStrength", "task", "RepetitionTime"]
            ])
            row.append(meta_str)
        
        table.add_row(*row)
    
    console.print(table)

def _display_csv(results: list[dict]):
    """Display search results as CSV."""
    import csv
    import sys
    
    if not results:
        return
    
    # Get all possible fields
    fieldnames = set()
    for result in results:
        fieldnames.update(result.keys())
        if "metadata" in result:
            fieldnames.update([f"metadata.{k}" for k in result["metadata"].keys()])
    
    fieldnames = sorted(fieldnames - {"metadata"})
    
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    writer.writeheader()
    
    for result in results:
        # Flatten metadata
        row = {k: v for k, v in result.items() if k != "metadata"}
        if "metadata" in result:
            for k, v in result["metadata"].items():
                row[f"metadata.{k}"] = v
        
        writer.writerow(row)

# Add completion callback for shell autocomplete
@app.command()
def _completion():
    """Generate shell completion script."""
    # Typer automatically handles this
    pass
```

## Example: Ingestion Command with Progress

```python
# src/neuropoly_db/cli/commands/ingest.py
import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from pathlib import Path
import asyncio
import time

from neuropoly_db.core.api_client import APIClient

app = typer.Typer(help="Data ingestion operations")
console = Console()

@app.command()
def ingest(
    dataset_path: Path = typer.Argument(
        ...,
        help="Path to BIDS dataset directory",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True
    ),
    index_name: str = typer.Option(
        None,
        "--index",
        "-i",
        help="Elasticsearch index name (default: neuroimaging-<dataset_id>)"
    ),
    async_mode: bool = typer.Option(
        False,
        "--async",
        "-a",
        help="Run ingestion in background (returns task ID)"
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Overwrite existing data"
    )
):
    """
    Ingest a BIDS dataset into Elasticsearch.
    
    Parses BIDS metadata, generates embeddings, and indexes scans.
    
    Examples:
        # Ingest dataset (blocking)
        neuropoly-db ingest /data/ds000001
        
        # Ingest in background
        neuropoly-db ingest /data/ds000001 --async
        
        # Overwrite existing data
        neuropoly-db ingest /data/ds000001 --overwrite
    """
    try:
        client = APIClient()
        
        # Determine index name
        if not index_name:
            dataset_id = dataset_path.name
            index_name = f"neuroimaging-{dataset_id}"
        
        console.print(f"[blue]i[/blue] Dataset: {dataset_path}")
        console.print(f"[blue]i[/blue] Index: {index_name}")
        
        # Start ingestion
        if async_mode:
            # Background mode
            response = asyncio.run(
                client.start_ingestion(
                    dataset_path=str(dataset_path),
                    index_name=index_name,
                    overwrite=overwrite
                )
            )
            
            task_id = response["task_id"]
            console.print(f"[green]✓[/green] Ingestion started: {task_id}")
            console.print(f"[blue]i[/blue] Monitor progress: neuropoly-db tasks status {task_id}")
        
        else:
            # Blocking mode with progress bar
            response = asyncio.run(
                client.start_ingestion(
                    dataset_path=str(dataset_path),
                    index_name=index_name,
                    overwrite=overwrite
                )
            )
            
            task_id = response["task_id"]
            
            # Poll task status
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                console=console
            ) as progress:
                
                task = progress.add_task("Ingesting...", total=100)
                
                while True:
                    status = asyncio.run(client.get_task_status(task_id))
                    
                    if status["state"] == "PROGRESS":
                        percent = status["progress"]["percent"]
                        current = status["progress"]["current"]
                        total = status["progress"]["total"]
                        
                        progress.update(
                            task,
                            completed=percent,
                            description=f"Processing {current}/{total} scans"
                        )
                    
                    elif status["state"] == "SUCCESS":
                        progress.update(task, completed=100)
                        result = status["result"]
                        console.print(
                            f"[green]✓[/green] Ingestion complete: "
                            f"{result['scans_indexed']} scans in "
                            f"{result['duration_seconds']:.1f}s"
                        )
                        break
                    
                    elif status["state"] == "FAILURE":
                        console.print(f"[red]✗[/red] Ingestion failed: {status['info']}", style="red")
                        raise typer.Exit(code=1)
                    
                    time.sleep(1)  # Poll every second
    
    except FileNotFoundError as e:
        console.print(f"[red]✗[/red] Dataset not found: {e}", style="red")
        raise typer.Exit(code=1)
    except ValueError as e:
        console.print(f"[red]✗[/red] Invalid dataset: {e}", style="red")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]✗[/red] Error: {e}", style="red")
        raise typer.Exit(code=1)
```

## Checklist

Before submitting your CLI command implementation, verify:

- [ ] Command uses `typer.Argument()` / `typer.Option()` with help text
- [ ] Rich console is used for colored output
- [ ] Error messages are user-friendly (no stack traces by default)
- [ ] Exit codes are set correctly (0=success, 1=error)
- [ ] Long-running operations show progress bars
- [ ] Async operations use `asyncio.run()` wrapper
- [ ] `--verbose` flag shows additional details
- [ ] Examples are included in docstring
- [ ] Path arguments use `Path` type with validation
- [ ] Output formats are supported (table, JSON, CSV)

## Related

- [Typer Documentation](https://typer.tiangolo.com/)
- [Rich Documentation](https://rich.readthedocs.io/)
- [ROADMAP.md](../docs/ROADMAP.md#phase-2-cli-tool-weeks-5-6)

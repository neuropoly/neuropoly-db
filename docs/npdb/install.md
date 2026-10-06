# NeuroPoly-DB CLI installation

This is about the `npdb` software installation to be able to download the data once being selected from the neurobagel webbrowser.

## Prerequisites

- Install [Python 3.12+](https://www.python.org/downloads/)
- Install [uv](https://docs.astral.sh/uv/getting-started/installation/)

## Installation

1. Create a **new virtual environment** locally to host the CLI dependencies and libraries :

   ```bash
   uv venv .venv
   ```

   > If you see "A virtual environment already exists at `.venv`. Do you want to replace it?", say "yes".

   > [!WARNING]
   > The above command _might fail if some virtual environment has already been configured in the provided directory (.venv)_. If you experience issues, **delete the content** under the virtual environment's directory and **re-run the command**.

2. Synchronize the virtual environment with the CLI dependencies :

   ```bash
   uv sync --active
   ```

> [!IMPORTANT]
> This project uses uv dependency groups for optional installs. Install the relevant group before running commands that depend on it:
>
> - Core CLI: `uv sync --active`
> - Development tools: `uv sync --active --group dev`
> - Full integration stack: `uv sync --active --group all`
> - Provider-specific installs: `uv sync --active --group git`, `uv sync --active --group gitea`, `uv sync --active --group openneuro`, etc.

### Assisted annotation and standardization

If you intend on using assisted or automated modes of the CLI (`npdb standardize bids`, `npdb convert bagel gitea`, or `npdb convert bagel local`), you need to install the automation extra. Run the following commands to install it:

```bash
uv sync --active --extra annotation-automation
uv run playwright install --with-deps chromium
```

After installation:

- use the [database ingestion workflows guide](./ingestion.md) to choose between `npdb convert bagel local` and `npdb convert bagel gitea`,
- then continue with the shared [annotation and standardization modes](./gitea2bagel/extended.md) if you need assisted or automated conversion.

### Development environment

To install the local development dependencies, run :

```bash
uv sync --active --group dev
```

> [!WARNING]
> If you need the full set of external integrations used by the project, install the `all` group with `uv sync --active --group all` before running commands that rely on those packages.

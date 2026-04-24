# NeuroPoly-DB CLI installation

## Prerequisites

- Install [Python 3.12+](https://www.python.org/downloads/)
- Install [uv](https://docs.astral.sh/uv/getting-started/installation/)

## Installation

1. Create a **new virtual environment** locally to host the CLI dependencies and libraries :

   ```bash
   uv venv .venv
   ```

   > [!WARNING]
   > The above command _might fail if some virtual environment has already been configured in the provided directory (.venv)_. If you experience issues, **delete the content** under the virtual environment's directory and **re-run the command**.

2. Synchronize the virtual environment with the CLI dependencies :

   ```bash
   uv sync --activate
   ```

### Assisted annotation and standardization

If you intend on using the assisted modes of the CLI, you need to install additional dependencies. Run the following commands to install them :

```bash
uv sync --active --quiet --extra annotation-automation
uv run playwright install --with-deps chromium
```

### Development environment

To install the full development environment, run :

```bash
uv sync --active --quiet --all-extras
```

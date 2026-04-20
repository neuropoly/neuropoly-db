# NEUROPOLY DATABASE EXPLORATION AND STANDARDIZATION TOOLS

This repository hosts a collection of tools to interact with **metadata contained in the several NEUROPOLY databases**.

> _Main Goal_
> Provide an exploration tool into every database, agnostic to the data structure (standard) and management software (e.g. DataLad, Git, etc.) used to store the data.

Components of the project :

- **[Database exploration](#database-exploration-using-neurobagel)**: Complete and structured deployment of a local [NeuroBagel](https://github.com/neurobagel) node.
- **[Metadata standardization](#metadata-standardization)**: A set of command line tools (under `npdb standardize`) to manipulate common standards (e.g. BIDS, Bagel).
- **[Database ingestion](#database-ingestion)**: A set of command line tools (under `npdb`) to ingest data into a local _NeuroBagel_ node (currently supports `Neurogitea` indexed databases only).

## Database exploration using NeuroBagel

### [NeuroBagel node installation](./docs/neurobagel/install.md)

### [NeuroBagel node management](./docs/neurobagel/manage.md)

> [!IMPORTANT]
> If **you are the only user of the NeuroBagel node**, we recommend using [VSCode](https://code.visualstudio.com/), with the [Remote Containers extension](https://code.visualstudio.com/docs/remote/containers) installed, and deploy the node using the [precrafted development container](./.devcontainer/devcontainer.json) in this repository.

## NeuroPoly-DB CLI

### Requirements

- [Python 3.10+](https://www.python.org/downloads/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

### Installation

```bash
uv venv .venv
uv sync --activate
```

> [!WARNING]
> The above command _might fail if some virtual environment has already been configured in the provided directory (.venv)_. If you experience issues, simply **delete the content** under the virtual environment's directory and **re-run the command**.

### Metadata standardization

#### BIDS `participants.tsv` standardization

The `npdb standarize bids` will :

- Standardize the header fields of provided `participants.tsv` file(s), following a given standard (the NeuroBagel standard by default).
- Add missing fields with empty values, following the same standard.
- Generate or update a `participants.json` file with associated standardized metadata fields descriptions.

##### Common usage

```bash
npdb standardize bids <bids_root_directory> \
   # (Optional) if not given, the script outputs in the input directory structure
   --output <output_directory> \
   # (Optional) standardization mode, default to 'manual'
   --mode <manual|assist|auto|full-auto>
```

> [!IMPORTANT]
> The assisted and automated modes (`assist`, `auto` and `full-auto`) require additional dependencies to be installed. Run :
>
> ```bash
> uv sync --active --quiet --extra annotation-automation
> uv run playwright install --with-deps chromium
> ```

> [!WARNING]
> The automated modes (`auto` and `full-auto`) use **state-of-the-art language models** to replace human intervention in all parts of the standardization process. **There is no guarantee that the generated output will be correct. Always check the generated output for potential errors**.

#### Custom header mapping

The `npdb standardize bids` command also accepts a custom header mapping file (`--header-map`), in JSON format, to specify the desired output headers and the input variants to consider for each of them. For example, the following mapping :

```json
{
  "age": ["age", "age_years", "years_old"],
  "sex": ["sex", "gender"]
}
```

will standardize any of the input variants (`age`, `age_years` or `years_old`) to the output header `age`, and any of the input variants (`sex` or `gender`) to the output header `sex`.

### Database ingestion

#### Neurogitea indexed databases

1. Copy the `template.env` file to a new `.env` file at the root of the repository :

   ```bash
   cp template.env .env
   ```

2. Edit the `.env` file to set your access credentials to the **Neurogitea database** you want to ingest from :

   - `NP_GITEA_APP_USER` : username to access Neurogitea.
   - `NP_GITEA_APP_TOKEN` : access token associated with the above username.
   - `NP_GITEA_APP_URL` : URL to the Neurogitea instance hosting the database.

3. Run the **dataset ingestion command** :

   ```bash
   npdb gitea2bagel <dataset_id> <output_directory>
   ```

   where :

   - `<dataset_id>` is the identifier of the dataset to ingest, as indexed in Neurogitea.
   - `<output_directory>` is the path where to output the `JSON-LD` files structured for ingestion by the NeuroBagel node.

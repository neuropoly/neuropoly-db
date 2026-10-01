# NEUROPOLY DATABASE EXPLORATION AND STANDARDIZATION TOOLS

This repository hosts a collection of tools to interact with **metadata contained in the several NEUROPOLY databases**.

- Streamlined setup of [NeuroBagel](https://neurobagel.org) nodes
- Parsing and standardization of [BIDS](https://bids.neuroimaging.io) datasets
- Automatic conversion of BIDS datasets to [NeuroBagel](https://neurobagel.org)
- Automated download from [NeuroBagel](https://neurobagel.org) queries
  - Support `http(s)`, `git` and `git-annex` protocols
    - Works with [Gitea](https://gitea.com) and [Forgejo](https://forgejo.org) out-of-the-box
  - Soon to be released specialized support for :
    - [Kaggle](https://www.kaggle.com)
    - [Mendeley Data](https://data.mendeley.com)
    - [MIDRC](https://midrc.org)
    - [OpenNeuro](https://openneuro.org)
    - [Zenodo](https://zenodo.org)
    - [Figshare](https://figshare.com) (partially supported via `http(s)` protocol)

## Contents

- [`npdb` command line tool](#npdb-command-line-tool)
  - [Prerequisites](#prerequisites)
  - [Provider managers and auth](#provider-managers-and-auth)
  - [Installation](#installation)
  - [Usage guides](#usage-guides)
  - [Commands](#commands)
  - [Developer guide](#developer-guide)
    - [Developer installation](#developer-installation)
    - [Components](#components)

## `npdb` command line tool

The **N**euro**P**oly **D**atabase **B**rowser is a python command line tool that **simplifies interaction** with the many databases and **hosting technologies** ([NeuroGitea](https://data.neuro.polymtl.ca), [NeuroBagel](https://neurobagel.org), etc.) used at NeuroPoly and their associated **data standards** ([DICOM](https://www.dicomstandard.org), [Nifti](https://nifti.nimh.nih.gov), [BIDS](https://bids.neuroimaging.io/index.html), etc.). It offers, among others, the following functionalities :

- [Standardization of BIDS datasets](#npdb-standardize-bids-options-bids_dir) to a common NeuroPoly vocabulary and structure.
- [Download of datasets from NeuroGitea](#npdb-download-options-query-resultstsv) using NeuroBagel queries.
- [Conversion of BIDS datasets to NeuroBagel](#npdb-convert-bagel-local-options-input_dir-online_url-output) format for ingestion in a NeuroBagel graph database.

All `npdb` commands are **interactive by default** and require user input to proceed. However, most of them also offer **assisted** and **automated** modes to reduce (even replace) user interaction and speed up the process. Refer to the [commands descriptions](#commands) below for more details.

> [!IMPORTANT]
> **New users are strongly encouraged to read the [usage guides](#usage-guides) before using the CLI.**

### Prerequisites

- Install [Python 3.12+](https://www.python.org/downloads/)
- Install [uv](https://docs.astral.sh/uv/getting-started/installation/)

### Installation

> [!IMPORTANT]
> **To use the `download` functionalities, you'll need to query from NeuroBagel (unless you already have the query results you need). Until an official NeuroBagel node is deployed at NeuroPoly, you need to install a local NeuroBagel node to query datasets from**.
>
> Follow the steps in [this documentation](./docs/neurobagel/user_install.md) to install and furnish a local NeuroBagel node.

0. If not done already, **clone or download this repository** to your local machine. Then, **open a terminal** and navigate to its root.

1. Create a **new virtual environment** locally to host the CLI dependencies and libraries :

   ```bash
   uv venv .venv
   ```

   Answer `yes` if you see :

   ```bash
   A virtual environment already exists at .venv. Do you want to replace it?
   ```

   The above command _might fail if some virtual environment has already been configured in the provided directory (.venv)_. If you experience issues, **delete the content** under the virtual environment's directory and **re-run the command**.

2. **Synchronize the virtual environment** with the CLI dependencies :

   ```bash
   uv sync --active
   ```

   > [!IMPORTANT]
   > The project uses uv dependency groups for optional installs. Use the correct group before running commands that depend on it:
   >
   > - Core CLI: `uv sync --active`
   > - Development tools: `uv sync --active --group dev`
   > - All optional integrations: `uv sync --active --group all`
   > - Provider-specific groups: `uv sync --active --group git gitea openneuro ...`

3. (Optional) If you intend on using the **assisted or automated modes** for BIDS standardization and conversion to NeuroBagel (see commands below), you need to **install the automation extra**. Run the following commands to install it :

    ```bash
    uv sync --active --extra annotation-automation
    uv run playwright install --with-deps chromium
    ```

   > [!WARNING]
   > The `annotation-automation` extra is required for the assisted and automated workflows used by `npdb standardize bids` and `npdb convert bagel` commands. Install it with `uv sync --active --extra annotation-automation` before running those commands.

### Provider managers and auth

The following provider-specific `npdb convert bagel` commands are supported as provider managers and wrappers around the same conversion pipeline used by the NeuroGitea flow:

- `npdb convert bagel git <repo_url> <output>`
- `npdb convert bagel kaggle <dataset_handle> <output>`
- `npdb convert bagel mendeley <dataset_id> <output>`
- `npdb convert bagel midrc <manifest.json> <output>`
- `npdb convert bagel openneuro <dataset_id> <output>`
- `npdb convert bagel zenodo <record_id_or_doi> <output>`
- `npdb convert bagel figshare <article_id_or_doi> <output>`

For providers that require credentials or a large local cache, the repository docs and the CLI help describe the exact env vars and setup steps. For example, Kaggle and archive-only Zenodo downloads require `--cache-dir` or `NP_NPDB_CACHE_DIR`; the CLI will stop with a clear error if it is missing because the download can be large.

See the provider guide: [docs/npdb/provider_managers.md](./docs/npdb/provider_managers.md).

### Usage guides

[This guide](./docs/npdb/download/guides/neurobagel_query.md) explains how to :
  
- **query datasets** using the `NeuroBagel` web interface,
- **save the query results** to file and interpret them,
- **download the query results** from `NeuroGitea` using `npdb`

[This guide](./docs/npdb/ingestion.md) explains how to :

- **convert a local BIDS dataset** with `npdb convert bagel local` when the dataset is already on disk,
- **convert a NeuroGitea/Forgejo-hosted dataset** with `npdb convert bagel gitea` when the dataset must be fetched from a forge first,
- **continue to the annotation and standardization modes** used by both workflows.

### Commands

#### `npdb standardize bids [options] <bids_dir>`

##### [🢖 Standardization options and customization](./docs/npdb/standardize/bids/extended.md)

![Standardize BIDS datasets](./docs/assets/npdb/npdb_standardize_bids.png)

#### `npdb download [options] <query-results.tsv>`

##### [🢖 **Guide**: download from NeuroBagel queries](./docs/npdb/download/guides/neurobagel_query.md)

![Download datasets from NeuroBagel](./docs/assets/npdb/npdb_download.png)

#### `npdb convert bagel local [options] <input_dir> <online_url> <output>`

![Local BIDS to NeuroBagel](./docs/assets/npdb/npdb_convert_bagel_local.png)

##### [🢖 **Guide**: ingest a local dataset into NeuroBagel](./docs/npdb/ingestion.md#local-bids-datasets-default-when-the-dataset-is-already-on-disk)

#### `npdb convert bagel gitea [options] <dataset> <output>`

![NeuroGitea or Forgejo to NeuroBagel](./docs/assets/npdb/npdb_convert_bagel_gitea.png)

##### [🢖 **Guide**: ingest a NeuroGitea/Forgejo dataset into NeuroBagel](./docs/npdb/ingestion.md#datasets-hosted-on-neurogiteaforgejo)

##### Which command should I use?

- Use `npdb convert bagel local` when the dataset is **already available locally**.
- Use `npdb convert bagel gitea` when the dataset must be **resolved from NeuroGitea/Forgejo**.

##### [🢖 Annotation and standardization modes](./docs/npdb/gitea2bagel/extended.md)

### Developer guide

#### Developer installation

First, run the [installation procedure above](#installation). Then, install the development and integration groups required for local work using :

```bash
uv sync --active --group dev
uv sync --active --group all
```

> [!WARNING]
> The `dev` group installs test and lint tools needed for development work, while the `all` group installs the provider integrations (`git`, `gitea`, `kaggle`, `mendeley`, `openneuro`, `zenodo`). Install the required group before running commands that rely on those packages.

#### Components

- **Database exploration**
  
  Complete and structured deployment of a local [NeuroBagel](https://github.com/neurobagel) node, extended with NeuroPoly-specific imaging modality vocabulary :

  - [NeuroBagel deployment](./docs/neurobagel/install.md)
  - [NeuroBagel extensions](./docs/neurobagel/extensions.md)
  - [NeuroBagel management](./docs/neurobagel/manage.md)

- **Database ingestion**

  A set of command line tools (under `npdb`) to ingest local or NeuroGitea/Forgejo-hosted BIDS datasets into a local _NeuroBagel_ node:

  - [Local and NeuroGitea/Forgejo database ingestion](./docs/npdb/ingestion.md)

- **Metadata standardization**
  
  A set of command line tools (under `npdb standardize`) to manipulate common standards (e.g. BIDS, Bagel).

  - [BIDS datasets standardization](./docs/npdb/standardization.md)

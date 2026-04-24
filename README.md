# NEUROPOLY DATABASE EXPLORATION AND STANDARDIZATION TOOLS

This repository hosts a collection of tools to interact with **metadata contained in the several NEUROPOLY databases**.

> _Main Goal_
> Provide an exploration tool into every database, agnostic to the data structure (standard) and management software (e.g. DataLad, Git, etc.) used to store the data.

Components of the project :

- **[Database exploration](#database-exploration-using-neurobagel)**: Complete and structured deployment of a local [NeuroBagel](https://github.com/neurobagel) node.
- **[Database ingestion](#database-ingestion)**: A set of command line tools (under `npdb`) to ingest data into a local _NeuroBagel_ node (currently supports `Neurogitea` indexed databases only).
- **[Metadata standardization](#metadata-standardization)**: A set of command line tools (under `npdb standardize`) to manipulate common standards (e.g. BIDS, Bagel).

## Database exploration using NeuroBagel

### [NeuroBagel node installation](./docs/neurobagel/install.md)

> [!IMPORTANT]
> If **you are the only user of the NeuroBagel node**, we recommend using [VSCode](https://code.visualstudio.com/), with the [Remote Containers extension](https://code.visualstudio.com/docs/remote/containers) installed, and deploy the node using the [precrafted development container](./.devcontainer/devcontainer.json) in this repository.

### [Querying the NeuroBagel node](./docs/neurobagel/query.md)

### [NeuroBagel node management](./docs/neurobagel/manage.md)

## NeuroPoly-DB CLI

### [NPDB CLI installation](./docs/npdb/install.md)

### [Database ingestion](./docs/npdb/ingestion.md)

### [Metadata standardization](./docs/npdb/standardization.md)

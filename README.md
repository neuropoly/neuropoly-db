# NEUROPOLY DATABASE EXPLORATION AND STANDARDIZATION TOOLS

This repository hosts a collection of tools to interact with **metadata contained in the several NEUROPOLY databases**.

> _Main Goal_
> Provide an exploration tool into every database, agnostic to the data structure (standard) and management software (e.g. DataLad, Git, etc.) used to store the data.

Components of the project :

- **[Database exploration](#database-exploration)**: Complete and structured deployment of a local [NeuroBagel](https://github.com/neurobagel) node.
- **[Database ingestion](#database-ingestion)**: A set of command line tools (under `npdb`) to ingest data into a local _NeuroBagel_ node (currently supports `Neurogitea` indexed databases only).
- **[Metadata standardization](#metadata-standardization)**: A set of command line tools (under `npdb standardize`) to manipulate common standards (e.g. BIDS, Bagel).

## Database exploration

> [!WARNING]
> To deploy a **production-ready NeuroBagel node**, refer to the [NeuroBagel documentation](https://neurobagel.org/user_guide/production_deployment) instead of the instructions below.

Database exploration is done through a _NeuroBagel Node_. The following describe how to deploy it locally **for development purposes**.

### Requirements

- [Docker](https://docs.docker.com/get-docker/) with [Docker Compose](https://docs.docker.com/compose/install/).

### Installation

> [!IMPORTANT]
> If **you are the only user of the NeuroBagel node**, we recommend using [VSCode](https://code.visualstudio.com/), with the [Remote Containers extension](https://code.visualstudio.com/docs/remote/containers) installed, and deploy the node using the [precrafted development container](./.devcontainer/devcontainer.json) in this repository.

With **Docker** installed, open a terminal and naviguate to the root of the repository. Run :

```bash
docker compose up -d
```

> [!TIP]
> If you have services or software running on your machine, **some of the ports used by NeuroBagel might be in use**. If the deployment fails, check the logs (`docker compose logs`) for occupied ports and change them in the `.env` file located at the root of the repository.

Once the deployment has completed, the NeuroBagel node should be accessible at [http://localhost:3000](http://localhost:3000) (or the port set for `NB_QUERY_PORT_HOST` in the `.env` file).

> [!IMPORTANT]
> The **default NeuroBagel node deployment** will ingest all data located under the `./seed-datasets` directory at the root of the repository. To select another directory, change the `LOCAL_GRAPH_DATA` variable in the `.env` file.

### Hot-Reloading Neurobagel with new datasets

In the root directory, run :

```bash
docker compose restart init_data
sleep 20
docker compose restart graph api federation query_federation
```

## NeuroPoly-DB CLI

### [NPDB CLI installation](./docs/npdb/install.md)

### [Database ingestion](./docs/npdb/ingestion.md)

### [Metadata standardization](./docs/npdb/standardization.md)

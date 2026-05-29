# NEUROPOLY DATABASE EXPLORATION AND STANDARDIZATION TOOLS

This repository hosts a collection of tools to interact with **metadata contained in the several NEUROPOLY databases**.

> _Main Goal_
> Provide an exploration tool into every database, agnostic to the data structure (standard) and management software (e.g. DataLad, Git, etc.) used to store the data.

## User guide

### [NeuroBagel node installation](./docs/neurobagel/user_install.md)

This step needs to be done in order to list all available datasets at NeuroPoly. If new datasets come in, this step needs to be done again. Currently, the node needs to be installed locally. Eventually, users won't need to do that as the node will be installed on a server at NeuroPoly. 

### [NeuroPoly-DB CLI installation](./docs/npdb/install.md)

This step installs the tool that is used to package and download datasets. You only need to do this step once.

### [Query and download datasets](./docs/neurobagel/query.md)

This step explains how to download data that are selected on the neurobagel web interface.


## Developer guide

Components of the project :

- **[Database exploration]** : Complete and structured deployment of a local [NeuroBagel](https://github.com/neurobagel) node, extended with NeuroPoly-specific imaging modality vocabulary.
- **[Database ingestion]**: A set of command line tools (under `npdb`) to ingest data into a local _NeuroBagel_ node (currently supports `Neurogitea` indexed databases only).
- **[Metadata standardization]**: A set of command line tools (under `npdb standardize`) to manipulate common standards (e.g. BIDS, Bagel).

### NeuroBagel

#### [NeuroBagel deployment](./docs/neurobagel/install.md)

#### [NeuroBagel extensions](./docs/neurobagel/extensions.md)

#### [NeuroBagel management](./docs/neurobagel/manage.md)

### NeuroPoly-DB CLI

#### [NPDB CLI installation](./docs/npdb/install.md)

#### [Database ingestion](./docs/npdb/ingestion.md)

#### [Metadata standardization](./docs/npdb/standardization.md)

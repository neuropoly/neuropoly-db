# NEUROPOLY DATABASE EXPLORATION AND STANDARDIZATION TOOLS

This repository hosts a collection of tools to interact with **metadata contained in the several NEUROPOLY databases**.

> _Main Goal_
> Provide an exploration tool into every database, agnostic to the data structure (standard) and management software (e.g. DataLad, Git, etc.) used to store the data.

## User guide

### [NeuroBagel node installation](./docs/neurobagel/user_install.md)

### [NeuroPoly-DB CLI installation](./docs/npdb/install.md)

### [Query and download datasets](./docs/neurobagel/query.md)

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

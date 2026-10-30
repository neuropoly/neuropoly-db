# NeuroPoly data tools

Find and download NeuroPoly imaging data in three steps: **search** the lab's datasets on the web, **export** your selection, and **download** it with one command.

```mermaid
flowchart LR
    A[Search on<br/>Neurobagel] --> B[Export a<br/>results file] --> C[npdb download] --> D[Data on<br/>your computer]
```

New here? Read **[What&#39;s available to you](docs/overview.md)** first.

## Get started

1. **[Get access](docs/get-access.md)**: VPN, GitHub organization, NeuroData account and token. One-time setup.
2. **[Install npdb](docs/install.md)**: follow the complete procedure, including system dependencies and build tools.
3. **[Find data](docs/find-data.md)** on Neurobagel: [https://data.neuro.polymtl.ca/bagel](https://data.neuro.polymtl.ca/bagel).
4. **[Download data](docs/download-data.md)**:

   ```bash
   npdb download results.tsv -o my-cohort
   ```

Walk through all of it with an example: **[Tutorial: your first cohort](docs/tutorial-first-cohort.md)**.

## Going further

- **Validate or repair a local BIDS-like dataset** with `bids-repair`, installed
  alongside `npdb`: [BIDS repair guide](docs/bids-repair.md).
- **Learn how lab data is organized** (Duke, NeuroData, Neurobagel): [wiki tutorials](https://github.com/neuropoly/neuropoly-db/wiki).
- **Add or curate datasets** (clean up, convert, publish on Neurobagel): [wiki tutorials, parts 4 and 5](https://github.com/neuropoly/neuropoly-db/wiki/Tutorial-4-NeuroData-to-Neurobagel).
- **Developers** (contributing to `npdb`, running a Neurobagel node, every command and option): [wiki Development section](https://github.com/neuropoly/neuropoly-db/wiki/Development).

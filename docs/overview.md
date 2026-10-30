# What's available to you

NeuroPoly keeps its imaging datasets in one place and lets you search them like a catalogue. You pick the subjects you need, and a small tool downloads them for you.

```mermaid
flowchart LR
    A[1. Search<br/>Neurobagel website] --> B[2. Export<br/>a results file]
    B --> C[3. Download<br/>npdb download]
    C --> D[Data on<br/>your computer]
```

## The three pieces

|                      | What it is                                                                                                           | Where                                                                     |
| -------------------- | -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| **Neurobagel** | A search website. Filter subjects by age, sex, diagnosis, type of scan... across all lab datasets at once.           | [https://data.neuro.polymtl.ca/bagel](https://data.neuro.polymtl.ca/bagel) |
| **NeuroData**  | The lab's data server. Every dataset lives there, organized in the [BIDS](https://bids.neuroimaging.io) format.       | [https://data.neuro.polymtl.ca](https://data.neuro.polymtl.ca)             |
| **npdb**       | A command you type in a terminal. It reads your Neurobagel results and downloads only those subjects from NeuroData. | your computer                                                             |

## What you can do

- **Browse** what data exists, without downloading anything:
  - Using queries (age, sex, imaging modality, etc.) on the [Neurobagel UI](https://data.neuro.polymtl.ca/bagel).
  - Introspecting the datasets available on [NeuroData](https://data.neuro.polymtl.ca/datasets).
- **Build a cohort**: using the Neurobagel UI:
  - e.g. "sex=female, imaging modality=T2-weighted,  minimal age=18".
  - Query result saved to `my-neurobagel-query-result.tsv`.
- **Download exactly that cohort**, sparsely from several datasets in one go:
  - `npdb download my-neurobagel-query-result.tsv`

## What you need

- The Polytechnique **VPN** when you're off campus.
- Be a member of the **[neuropoly GitHub organization](https://github.com/neuropoly)** (to open Neurobagel).
- A **NeuroData account** (to download using `npdb`).

## What to do now

1. [Get access](get-access.md)
2. [Install npdb](install.md)
3. [Find data](find-data.md)
4. [Download data](download-data.md)
5. Put it together: [Tutorial: your first cohort](tutorial-first-cohort.md)

Want to add or curate datasets, or go further? See the [wiki](https://github.com/neuropoly/neuropoly-db/wiki).

# Database ingestion workflows

Use this guide to choose the right `npdb convert bagel` entry point before ingesting datasets into a local NeuroBagel node.

| If your dataset... | Use this command |
|--------------------|------------------|
| is already present on your machine or shared storage | `npdb convert bagel local` |
| must be identified and fetched from NeuroGitea/Forgejo | `npdb convert bagel gitea` |

Both commands feed the same annotation and conversion pipeline. After conversion, place the generated JSON-LD files in the location used by your NeuroBagel node and reload the node as described in [NeuroBagel node management](../neurobagel/manage.md#update-the-datasets-available).

## Local BIDS datasets (default when the dataset is already on disk)

Use `npdb convert bagel local` when you already have the BIDS dataset in a local directory, on shared storage, or in a location that does not need to be resolved from NeuroGitea/Forgejo first.

### Prerequisites

1. Install the `npdb` CLI by following the [CLI installation guide](./install.md).
2. If you plan to use `--mode assist`, `--mode auto`, or `--mode full-auto`, install the additional browser automation dependencies from the [installation guide](./install.md#assisted-annotation-and-standardization).
3. Prepare or deploy a local NeuroBagel node by following the [NeuroBagel user installation guide](../neurobagel/user_install.md), so you have a destination for the generated JSON-LD files.

### Ingestion

Run the local conversion command:

```bash
npdb convert bagel local <input_dir> <online_url> <output_directory> [--access-type <value>]
```

where:

- `<input_dir>` is the root directory of the local BIDS dataset to convert.
- `<online_url>` is the URL written into the generated `RepositoryURL` and `AccessLink` metadata fields. Use a stable landing page, repository URL, DOI page, or other online location that explains how users can find or request the dataset.
- `<output_directory>` is the directory where the command writes the generated NeuroBagel `JSON-LD` files for ingestion.
- `--access-type` records the dataset access conditions in the output metadata. If omitted, it defaults to `restricted`. Use another value such as `public` when that better reflects how the dataset can be accessed.

### What this workflow produces

This workflow runs the same annotation and conversion pipeline as the forge-backed command, but starts from your local BIDS directory. The output directory contains the `JSON-LD` files that NeuroBagel ingests into its graph.

Once the files are generated:

1. Place them in `./seed-datasets` or in the directory configured through `LOCAL_GRAPH_DATA`.
2. Reload the node by following [Update the datasets available](../neurobagel/manage.md#update-the-datasets-available) or [Hot-reloading](../neurobagel/manage.md#hot-reloading).

### Next step: choose an annotation mode

Both `local` and `gitea` variants support the same annotation and standardization modes. Continue with [Annotation and standardization modes](./gitea2bagel/extended.md) to decide whether to run manually, assisted, or automatically.

## Datasets hosted on NeuroGitea/Forgejo

Use `npdb convert bagel gitea` when the dataset must be resolved from a NeuroGitea or Forgejo instance before conversion.

### Prerequisites

> [!WARNING]
> Before setting up your NeuroGitea access or running dataset ingestion, install the required uv dependency group if it is not already available:
>
> ```bash
> uv sync --active --group gitea
> ```
>
> This group provides the Gitea client library used by the ingestion commands.

> [!IMPORTANT]
> Follow [these instructions](../neurogitea/account.md) to setup your [NeuroGitea](https://data.neuro.polymtl.ca) account (token and ssh keys) for automated access.

1. Copy the `template.env` file to a new `.env` file at the root of the repository (if not done already) :

   ```bash
   cp template.env .env
   ```

2. Edit the `.env` file to set your access credentials to the **NeuroGitea/Forgejo instance** you want to ingest from :

   - `NP_GITEA_APP_USER` : username to access the forge.
   - `NP_GITEA_APP_TOKEN` : access token associated with the above username.
   - `NP_GITEA_APP_URL` : URL to the NeuroGitea/Forgejo instance hosting the dataset.

If you plan to use assisted or automated annotation modes after the dataset is fetched, also install the additional browser automation dependencies from the [CLI installation guide](./install.md#assisted-annotation-and-standardization).

### Ingestion

Run the **dataset ingestion command** :

   ```bash
   npdb convert bagel gitea <dataset_id> <output_directory>
   ```

   where :

   - `<dataset_id>` is the identifier of the dataset to ingest, as indexed in the NeuroGitea/Forgejo instance.
   - `<output_directory>` is the path where to output the `JSON-LD` files structured for ingestion by the NeuroBagel node.

### Extending imaging modality support

NeuroBagel's `bagel` CLI natively supports 8 standard BIDS suffixes (`T1w`, `T2w`, `dwi`, `bold`, `asl`, `eeg`, `meg`, `pet`). NeuroPoly datasets frequently use non-standard suffixes such as `TEM`, `BF`, `PLI`, `UNIT1` or `T2star`. Pass `--extend-modalities` to let the ingestion pipeline handle these automatically instead of failing:

```bash
npdb convert bagel gitea <dataset_id> <output_directory> --extend-modalities
```

#### How suffix resolution works

When `--extend-modalities` is set, each unsupported suffix is resolved in the following order:

| Priority | Source | Description |
|----------|--------|-------------|
| 1 | **Extensions cache** | Already-resolved suffixes stored in `config/imaging_extensions.json` — resolved immediately with no I/O. |
| 2 | **NeuroPoly vocab** | `config/neuropoly_imaging_modalities.json` — the 13 custom `nb:` terms maintained by NeuroPoly (microscopy, tomography, `T2star`, …). |
| 3 | **NIDM aliases** | Hardcoded MRI variants that map to existing standard NIDM terms (`UNIT1`, `MP2RAGE`, `T1map`, `T2starmap`, `SWI`, `angio`, …). |
| 4 | **LLM** | When `--ai-provider` and `--ai-model` are given, an LLM is queried to propose the best IRI. New `nb:` terms are automatically promoted into the NeuroPoly vocab file. |
| 5 | **Generic fallback** | `nb:Custom{Suffix}Image` — always succeeds. Used when no other step resolves the suffix. |

#### Using an LLM for unknown suffixes

For datasets with suffixes not yet in the NeuroPoly vocab, pair `--extend-modalities` with AI options:

```bash
npdb convert bagel gitea <dataset_id> <output_directory> \
    --extend-modalities \
    --ai-provider ollama \
    --ai-model neural-chat
```

New terms the LLM resolves as `nb:` IRIs are automatically added to `config/neuropoly_imaging_modalities.json` so they are available to the API query UI and future runs without repeating the LLM call.

#### The NeuroPoly vocab file

`config/neuropoly_imaging_modalities.json` is the **single source of truth** for NeuroPoly's custom imaging modality terms. It is used by both the CLI (step 2 above) and the NeuroBagel API container (to display human-readable labels in the query interface).

Currently supported custom terms:

| BIDS suffix | IRI | Label |
|-------------|-----|-------|
| `BF`    | `nb:BrightFieldMicroscopy`                          | Bright-field microscopy |
| `DF`    | `nb:DarkFieldMicroscopy`                            | Dark-field microscopy |
| `PC`    | `nb:PhaseContrastMicroscopy`                        | Phase-contrast microscopy |
| `DIC`   | `nb:DifferentialInterferenceContrastMicroscopy`     | Differential interference contrast microscopy |
| `FLUO`  | `nb:FluorescenceMicroscopy`                         | Fluorescence microscopy |
| `CONF`  | `nb:ConfocalMicroscopy`                             | Confocal microscopy |
| `PLI`   | `nb:PolarisedLightImaging`                          | Polarised light imaging |
| `TEM`   | `nb:TransmissionElectronMicroscopy`                 | Transmission electron microscopy |
| `SEM`   | `nb:ScanningElectronMicroscopy`                     | Scanning electron microscopy |
| `uCT`   | `nb:MicroComputedTomography`                        | Micro-computed tomography |
| `OCT`   | `nb:OpticalCoherenceTomography`                     | Optical coherence tomography |
| `CARS`  | `nb:CoherentAntiStokesRamanSpectroscopyMicroscopy`  | CARS microscopy |
| `T2star`| `nb:T2StarWeighted`                                 | T2\*-weighted image |

To add new terms or understand the schema, see [Managing the imaging modality vocabulary](../neurobagel/manage.md#custom-imaging-modality-vocabulary).

### `vocab_extension_pending` warnings

When the pipeline cannot write a new term to the vocab file (e.g. due to a permission error, or because the LLM returned an IRI that fails validation), the run ledger entry for that dataset will contain a `vocab_extension_pending` list:

```json
{
  "status": "success",
  "vocab_extension_pending": [
    "vocab_extension_pending: could not promote 'XMod' → 'nb:XModality' into config/neuropoly_imaging_modalities.json: [Errno 13] Permission denied. Add manually."
  ]
}
```

The dataset conversion **still succeeds** — the IRI is written into the JSON-LD graph. However, the query UI will show a blank label instead of a human-readable name until the term is added manually. Follow the instructions in [Managing the imaging modality vocabulary](../neurobagel/manage.md#custom-imaging-modality-vocabulary) to resolve these warnings.

After conversion, continue with the shared [annotation and standardization modes documentation](./gitea2bagel/extended.md) if you need details about `--mode manual`, `--mode assist`, `--mode auto`, or `--mode full-auto`.

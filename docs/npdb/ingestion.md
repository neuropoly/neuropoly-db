# Database ingestion procedure

## Neurogitea indexed databases

1. If not already done, copy the `template.env` file to a new `.env` file at the root of the repository :

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

### Extending imaging modality support

NeuroBagel's `bagel` CLI natively supports 8 standard BIDS suffixes (`T1w`, `T2w`, `dwi`, `bold`, `asl`, `eeg`, `meg`, `pet`). NeuroPoly datasets frequently use non-standard suffixes such as `TEM`, `BF`, `PLI`, `UNIT1` or `T2star`. Pass `--extend-modalities` to let the ingestion pipeline handle these automatically instead of failing:

```bash
npdb gitea2bagel <dataset_id> <output_directory> --extend-modalities
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
npdb gitea2bagel <dataset_id> <output_directory> \
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

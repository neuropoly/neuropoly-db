
# NeuroPoly custom NeuroBagel extensions

## Custom imaging modality vocabulary

The stock NeuroBagel API only knows the 8 standard BIDS imaging modalities. This deployment extends the API to also display human-readable labels for the **13 NeuroPoly-specific custom terms** (microscopy, tomography, `T2star`, …) that live in `config/neuropoly_imaging_modalities.json`.

This is done transparently at container start-up via two files mounted into the `api` service:

| Mounted file | Container path | Purpose |
|---|---|---|
| `scripts/neuropoly_api_entrypoint.sh` | `/usr/src/api_entrypoint.sh` | Replaces the stock entrypoint; runs the vocab patch then starts uvicorn. |
| `scripts/inject_vocab_patch.py` | `/usr/src/neurobagel/inject_vocab_patch.py` | Writes a `sitecustomize.py` snippet that merges custom terms with the standard vocab on every `/imaging_modalities` request. |
| `config/neuropoly_imaging_modalities.json` | `/usr/src/neurobagel/neuropoly_imaging_modalities.json` | The NeuroPoly vocab file read by both the patch and the CLI. |

> [!NOTE]
> No changes to `neurobagel-recipes/` are required. The `docker-compose.yml` at the root of this repository uses a Docker Compose `!override` tag on the `api` service volumes list to inject these files without modifying the upstream recipes submodule.

To add or edit custom imaging modality terms, edit `config/neuropoly_imaging_modalities.json` and restart the API container:

```bash
docker compose restart api
```

See [Managing the imaging modality vocabulary](./manage.md#custom-imaging-modality-vocabulary) for the full schema and workflow.

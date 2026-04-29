# NeuroBagel node management

## Update the datasets available

Place the new datasets (`.jsonld` files) in the `./seed-datasets` directory at the root of the repository, **or the directory set in the `LOCAL_GRAPH_DATA` variable, if you edited the `.env` file**.

Once done, re-deploy the node with :

```bash
docker compose restart
```

## Hot-reloading

To avoid re-deploying the node every time you want to update the datasets, you can use the hot-reloading feature of NeuroBagel. This feature allows you to update the datasets without stopping the node.

```bash
docker compose restart init_data
echo "Waiting for init_data to complete..." && sleep 5
docker compose restart graph api
echo "Waiting for graph and api services to restart..." && sleep 20
```

## Custom imaging modality vocabulary

`config/neuropoly_imaging_modalities.json` is the **single source of truth** for NeuroPoly's custom imaging modality terms. It drives two things simultaneously:

1. **CLI ingestion** (`uv run npdb gitea2bagel --extend-modalities`): resolves BIDS suffixes to `nb:` IRIs before calling `bagel`.
2. **Query UI labels**: the NeuroBagel API container reads this file at start-up and injects the human-readable term names into the `/imaging_modalities` vocabulary endpoint.

### Adding a new term

Edit `config/neuropoly_imaging_modalities.json` and add an entry to the `terms` array of the `nb` namespace block:

```json
{
  "namespace_prefix": "nb",
  "namespace_url": "http://neurobagel.org/vocab/",
  "vocabulary_name": "NeuroPoly extended imaging modality terms",
  "version": "1.0.0",
  "terms": [
    ...
    { "name": "My new modality", "id": "MyNewModality", "abbreviation": "MNM" }
  ]
}
```

| Field | Description | Constraints |
|-------|-------------|-------------|
| `name` | Human-readable label shown in the query UI. | Any string. |
| `id` | CamelCase local name of the `nb:` IRI (`nb:<id>`). | Must match `[a-zA-Z][a-zA-Z0-9_]+`. |
| `abbreviation` | BIDS suffix used in your dataset. | Must be unique across all terms. |

After saving, restart the API container so it picks up the new label:

```bash
docker compose restart api
```

Re-ingest any datasets that use the new suffix with `uv run npdb gitea2bagel --extend-modalities` to write the correct IRI into their JSON-LD files.

### Resolving `vocab_extension_pending` warnings

When `uv run npdb gitea2bagel --extend-modalities` resolves a new `nb:` term via the LLM or generic fallback but **cannot write it** to the vocab file (e.g. a permission error), the run ledger entry will contain:

```json
"vocab_extension_pending": [
  "vocab_extension_pending: could not promote 'XMod' → 'nb:XModality' into config/neuropoly_imaging_modalities.json: [Errno 13] Permission denied. Add manually."
]
```

The dataset conversion itself succeeded — the IRI is already in the graph. To make the label appear in the query UI, add the missing term by hand following the [schema above](#adding-a-new-term), then restart the API.

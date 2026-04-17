# Seed Datasets Directory

Place all JSON-LD files that should be automatically loaded into the Neurobagel node in this directory.

## How It Works

When the devcontainer starts, the `init_data` service will:
1. Read all `.jsonld` files from this directory
2. Validate them against the Neurobagel schema
3. Load them into the GraphDB instance
4. Create the `datasets_metadata.json` file automatically
5. Make datasets immediately available in the Query UI

## Usage

### Option 1: Copy JSON-LD files here

```bash
cp /path/to/your/dataset.jsonld /workspaces/neuropoly-db/seed-datasets/
```

### Option 2: Symlink from elsewhere

```bash
ln -s /path/to/your/whole-spine.jsonld /workspaces/neuropoly-db/seed-datasets/whole-spine.jsonld
```

### Option 3: Generate and save

```bash
# From inside the devcontainer:
npdb gitea2bagel my-dataset --output ./my_output
cp ./my_output/whole-spine.jsonld /seed-datasets/
```

## Hot-Reloading

In the root directory, run :

```bash
docker compose restart init_data
docker compose restart graph api federation query_federation
```

## Rebuild Devcontainer

After adding JSON-LD files, rebuild the devcontainer to process them:

**In VS Code**: `Ctrl+Shift+P` → `Dev Containers: Rebuild Container`

---

**Note**: Files are read at build time only. To add datasets to a running node, use the API directly or restart the services.

## Example

The benchmark whole-spine dataset can be seeded with:

```bash
ln -s /workspaces/neuropoly-db/benchmark/whole-spine.jsonld /workspaces/neuropoly-db/seed-datasets/whole-spine.jsonld
```

Then rebuild the devcontainer.

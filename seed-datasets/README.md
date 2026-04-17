# Seed Datasets Directory

Place all JSON-LD files that should be automatically loaded into the Neurobagel node in this directory.

## How It Works

When the docker compose stack starts, the `init_data` service will:

1. Read all `.jsonld` files from this directory
2. Validate them against the Neurobagel schema
3. Load them into the GraphDB instance
4. Create the `datasets_metadata.json` file automatically
5. Make datasets immediately available in the Query UI

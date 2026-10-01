# Provider managers and authentication

This repository exposes a provider-backed conversion flow under `npdb convert bagel` for datasets that are not stored directly on a local filesystem or on Gitea/Forgejo.

The shared conversion pipeline remains the same as the local and Gitea flows: each provider manager fetches the source dataset into a local directory, then calls the standard Neurobagel conversion step.

## Shared cache rule

Providers that download large archives or dataset bundles should use a local cache directory. The CLI enforces this when a provider is expected to fetch a large artifact or an archive-only record.

- Set `NP_NPDB_CACHE_DIR` in your environment or pass `--cache-dir` on the CLI.
- The command will fail with a clear message if the provider needs a cache and none is provided.
- This avoids silently downloading very large archives into a temporary directory that may be cleaned up unexpectedly.

## Supported commands

```bash
npdb convert bagel git <repo_url> <output>
npdb convert bagel kaggle <dataset_handle> <output>
npdb convert bagel mendeley <dataset_id> <output>
npdb convert bagel midrc <manifest.json> <output>
npdb convert bagel openneuro <dataset_id> <output>
npdb convert bagel zenodo <record_id_or_doi> <output>
npdb convert bagel figshare <article_id_or_doi> <output>
```

## Environment variables

Copy the values from `template.env` into a local `.env` file and fill in the credentials required for the provider you want to use.

### Git / generic HTTP

- `NP_GIT_USER`
- `NP_GIT_TOKEN`

Use these for generic Git HTTP repositories that require basic authentication.

### Kaggle

- `NP_KAGGLE_USERNAME`
- `NP_KAGGLE_KEY`

Create a Kaggle API token from your Kaggle account settings and paste the values into the environment file. Public datasets do not require credentials, but private datasets do.

### OpenNeuro

- `NP_OPENNEURO_TOKEN`

This is optional for public datasets. If your dataset is private or restricted, add your OpenNeuro API token before running the command.

### Zenodo

- `NP_ZENODO_TOKEN`
- `NP_NPDB_CACHE_DIR`

Public Zenodo records can work without a token, but some embargoed, restricted, or archive-only records require a token and a cache directory for the download.

### MIDRC

- `NP_MIDRC_CREDENTIALS`
- `NP_MIDRC_ENDPOINT` (defaults to `https://data.midrc.org`)

Download the `credentials.json` file from your MIDRC profile and point `NP_MIDRC_CREDENTIALS` to it. The endpoint is usually the default value.

### Figshare

- `NP_FIGSHARE_TOKEN`

Figshare public records can be fetched without a token. Private or access-controlled content requires a token.

### Mendeley

- `NP_MENDELEY_CLIENT_ID`
- `NP_MENDELEY_CLIENT_SECRET`
- `NP_MENDELEY_ACCESS_TOKEN`

Mendeley requires app credentials or an access token for restricted data. Public archives may work without authentication, depending on the dataset policy.

## Setup procedure

1. Copy the repository template file:

   ```bash
   cp template.env .env
   ```

2. Edit `.env` and fill in only the variables relevant to the provider you plan to use.
3. Export or load the environment before invoking the CLI:

   ```bash
   set -a
   source .env
   set +a
   ```

4. Run the conversion command:

   ```bash
   npdb convert bagel zenodo <record_id> <output_dir>
   ```

5. If the command complains that `NP_NPDB_CACHE_DIR` is missing, set it to a writable local directory with enough free space for the archive download.

## Notes for large downloads

The `--cache-dir` option is intended for data that is large enough to justify a persistent local staging area. This is especially important for Kaggle and archive-based Zenodo downloads, where a temporary folder can be deleted before the conversion step finishes or can consume too much space unexpectedly.

When you are preparing a conversion pipeline for production, prefer a dedicated workspace directory such as:

```bash
export NP_NPDB_CACHE_DIR="$HOME/.cache/npdb-downloads"
```

This keeps the dataset payloads close to the workspace and makes retries and reprocessing predictable.

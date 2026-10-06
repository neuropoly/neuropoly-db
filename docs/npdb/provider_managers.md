# Provider managers and authentication

This repository exposes a provider-backed conversion flow under `npdb convert bagel` for datasets that are not stored directly on a local filesystem or on Gitea/Forgejo.

The shared conversion pipeline remains the same as the local and Gitea flows: each provider manager fetches the source dataset into a local directory, then calls the standard Neurobagel conversion step.

Before conversion, provider downloads are checked for ZIP and TAR archives
(including gzip, bzip2, and xz compressed TAR files). Each archive is extracted
into the directory containing it and deleted only after successful extraction.
Ordinary files are unchanged, and archives inside newly extracted content are
not recursively unpacked. Unsafe archive paths, links, and extraction failures
stop conversion; the failed archive is retained.

After extraction, if no `derivatives` directory exists, non-hidden top-level
directories other than `rawdata`, `sub-*`, `sourcedata`, `stimuli`, `code`,
and `phenotype` directories are moved into a new
`derivatives` directory. Existing derivatives layouts are left unchanged.
The contents of `rawdata` are then moved to the dataset root and the empty
directory is removed. Conflicting destination names stop preparation rather
than overwriting existing data.

If `participants.tsv` is missing, preparation generates it with
`participant_id`, `age`, and `sex` columns, using unique BIDS `sub-<label>`
entities from raw-data filenames and directories. Hidden paths, derivatives,
`sourcedata`, `stimuli`, `code`, and `phenotype` are excluded from subject discovery.
Age and sex are filled with `N/A`; existing tables are preserved.
If no subject labels can be found, preparation fails explicitly.

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
npdb convert bagel midrc <dataset_url_or_id_or_guid_or_manifest> <output>
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
- `NP_NPDB_CACHE_DIR` (or `--cache-dir`; required for Discovery datasets)

MIDRC accepts **Discovery dataset URLs or IDs**, **file GUIDs**, and local
Gen3 manifests. For example:

```bash
npdb convert bagel midrc https://data.midrc.org/discovery/H6K0-A61V/ ./output \
  --credentials-path /absolute/path/to/credentials.json \
  --cache-dir /absolute/path/to/midrc-cache
```

Dataset IDs resolve through MIDRC's Discovery metadata API and download all
linked files. File GUIDs appear in the **Object Id** column in
**Exploration > Data Files**; copy the full `dg.MD1R/` prefix. To create a manifest, sign in, select
a cohort in Exploration, and use **Download file manifest**, not
**Download table**.

MIDRC permits anonymous discovery, but its open-access resources are intended
for registered users under a Data Use Agreement. Anonymous checks of one
indexed file returned HTTP 401 from the download API and HTTP 403 from storage.
For authenticated Gen3 downloads, create an API key on your MIDRC **Profile**
page and save the downloaded `credentials.json`.

Downloads use authenticated Gen3 signed URLs, streaming, and indexed file
size/checksum verification. Cached archives are copied into the fetch directory
so shared extraction does not delete the reusable cache.

The existing shared pipeline extracts archives, adjusts the layout, and converts
metadata. It does not apply dataset-specific clinical joins, invent BIDS names,
or convert DICOMs. Incompatible source layouts fail explicitly.

See the [MIDRC GUID, manifest, and download guide](./download/guides/midrc.md)
for exact input shapes, verified access checks, the supported Gen3 download
workflow, caching behavior, and conversion-layout requirements.

### Figshare

- `NP_FIGSHARE_TOKEN`

Figshare public records can be fetched without a token. Private or access-controlled content requires a token.

The Figshare argument accepts an article ID, a bare DOI, or a `https://doi.org/`
URL. Collection DOIs such as `10.6084/m9.figshare.c.7372564` are resolved through
the collection's paginated article list, and files from every member article
are downloaded. Files are staged in a shared directory; duplicate filenames
within a fetch raise an error rather than silently overwriting another article's
files. Re-running a fetch can replace files from a previous run.
File downloads follow HTTP redirects, including Figshare's redirects to signed
storage URLs. Errors from the final download endpoint are still reported.

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

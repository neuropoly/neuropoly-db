# Validate and repair a local BIDS-like dataset

**`bids-repair`** receives a local BIDS-like directory. It owns generic BIDS validation, structural repair, layout normalization, tables, and sidecars.

## Install

Follow the [installation guide](install.md) to install npdb and `bids-repair`,
including system dependencies, the Rust toolchain, and the bundled validator.

## Plan, validate, apply, recover

```bash
# Default operation: a read-only repair plan.
bids-repair ./my-bids --bids-version 1.11.1 --dry-run

# Read-only validation, without proposing/applying repairs.
bids-repair validate ./my-bids --bids-version 1.11.1 --format json

# Explicitly apply supported safe repairs, then revalidate.
bids-repair ./my-bids --bids-version 1.11.1 --apply

# Roll back an incomplete journaled repair. No version is needed.
bids-repair recover ./my-bids
```

The target version comes from:

- The `--bids-version` argument
- A valid `BIDSVersion` in the supplied `dataset_description.json`
- Interactively via the command-line

### Target version and validation coverage

The native beta supports **BIDS 1.11.1 only**. Incompatible requested targets fail
before validation, schema downloads or repair. `--schema` accepts only a local
JSON schema (or compiled source directory) structurally identical to the bundled
schema; a different schema with the same version is not sufficient:

```bash
bids-repair ./my-bids --bids-version 1.11.1 --schema /path/to/schema-1.2.1.json
```

Historical/custom schemas cannot retarget the native engine, and are no longer
downloaded automatically. `--bids-version latest` still performs a network lookup
and fails if the resulting version is unsupported.

### Repair controls and safety

| Argument                                         | Description                                                                                                                                                                                              |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--suffix-alias OLD=NEW`                       | supplies an explicit suffix translation; repeat it for multiple translations. No scientific alias is inferred from provider identity.                                                                   |
| `--bids-severity-threshold info\|warning\|error` | controls the **final** repair gate:<br />only findings strictly above it fail. The default `warning` permits warnings,<br />but fails remaining errors. Validation-only commands fail on errors. |
| `--format json`                                | returns a machine-readable result on stdout. Repair summaries<br />and manifest locations are written to stderr.                                                                                         |
| `--apply`                                      | planning is read-only; use to apply the changes to the actual dataset                                                                                                                                    |
| `--dry-run`                                    | runs through the application code without calling the commands                                                                                                                                           |

Errors belonging to derivative datasets are treated as warnings for the final
gate and summary counts. The original validator report is retained unchanged;
adjusted findings record `original_severity`. Use a threshold of `info` to fail
on derivative warnings as well. Findings retain their owning dataset so identical
issues in different derivative datasets remain distinct across revalidation.

## Explicit participant standardization

```bash
bids-repair standardize ./my-bids --bids-version 1.11.1 \
  --header-map headers.json --column-metadata columns.json --dry-run
```

- `--header-map` accept arbitrary desired BIDS column names and will convert all matched `aliases` to them:

  ```json
  {
    "participant_id": {"aliases": ["SubjectID", "subject_id"]},
    "measurement": {"aliases": ["LocalMeasure"]}
  }
  ```
- `--column-metadata` supplies plain BIDS sidecar entries used to fill the `participants.json` schema file:

  ```json
  {
    "measurement": {"Description": "Local measurement", "Units": "mm"}
  }
  ```
- Use `--add-column NAME` to explicitly add a missing column filled with `n/a`.
- Use `--prepare-layout` to explicitly run the existing generic layout preparation
  (including rawdata flattening and missing participants-table generation).

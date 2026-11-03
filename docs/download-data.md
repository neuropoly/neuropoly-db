# Download data

`npdb download` reads your [results file](find-data.md) and downloads only those subjects.

## Before you start

- VPN on (off campus).
- [npdb installed](install.md).
- A `.env` file with your NeuroData credentials in your data folder ([Get access, step 5](get-access.md#5-save-your-credentials)).
- An exported [results file](find-data.md)

## Download

1. Open your data folder:

   ```bash
   cd ~/neuropoly-data
   ```

2. Trigger the download:

   ```bash
   npdb download results.tsv -o my-cohort
   ```

   Replace `results.tsv` with your export filename.

3. Check the output:

   ```bash
   ls my-cohort
   ```

   Expect one folder per dataset, containing selected subjects and sessions.
   Available derivatives are included by default for git-backed datasets.

## Options

| Option | Use |
|--------|-----|
| `-o <folder>` | Set the output folder; default is the current folder. |
| `--no-derivatives` | Download raw data only for git-backed datasets. |
| `-v` | Show git commands. |

## Troubleshooting

| Failure | Action |
|---------|--------|
| Missing `NP_GITEA_APP_...` variables | Run from your data folder and check `.env`. |
| Authentication / `401` / `403` | Check the username, token, and read permissions. |
| Connection timeout | Check VPN access. |

For unresolved failures, share the command and error on lab Slack, without credentials.

Example: [Your first cohort](tutorial-first-cohort.md).

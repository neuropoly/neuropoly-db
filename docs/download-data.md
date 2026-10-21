# Download data

`npdb download` reads your [results file](find-data.md) and downloads only those subjects.

## Before you start

- VPN on (off campus).
- [npdb installed](install.md).
- A `.env` file with your NeuroData credentials in your data folder ([Get access, step 5](get-access.md#5-save-your-credentials)).

## Download

Go to your data folder and run:

```bash
cd ~/neuropoly-data
npdb download --no-verify-ssl results.tsv -o my-cohort
```

Replace `results.tsv` with the name of your results file, and `my-cohort` with the folder to download into.

> [!IMPORTANT]
> `--no-verify-ssl` is **required** for now: the NeuroData server certificate is not yet recognized by default. This is safe because NeuroData is only reachable through the VPN.

## What you get

One folder per dataset, organized in [BIDS](https://bids.neuroimaging.io):

```text
my-cohort/
├── canproco/
│   ├── participants.tsv
│   ├── sub-cal056/
│   │   └── ses-M0/anat/sub-cal056_ses-M0_T2w.nii.gz
│   └── derivatives/      # processed files (e.g. segmentations), when available
└── dcm-oklahoma/
    └── ...
```

## Useful options

| Option | What it does |
|--------|--------------|
| `-o <folder>` | Where to save the data (default: the current folder). |
| `--no-derivatives` | Skip processed files, download raw data only. |
| `-v` | Show more details while downloading. |

## If it fails

| Message | What to do |
|---------|------------|
| `Missing required environment variables: NP_GITEA_APP_...` | You're not in the folder with your `.env`, or it's incomplete. See [Get access, step 5](get-access.md#5-save-your-credentials). |
| Authentication / `401` / `403` errors | Your token is wrong or expired: [create a new one](get-access.md#4-create-a-download-token). |
| Connection timeouts | Turn on the VPN. |

Still stuck? Ask on the lab Slack, and share the command you ran and the error.

Next: [Tutorial: your first cohort](tutorial-first-cohort.md)

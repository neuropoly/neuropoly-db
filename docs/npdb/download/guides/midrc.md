# MIDRC: file GUIDs, manifests, and download access

Verified on **2026-10-06** against the live MIDRC portal, its public file
index, and official Gen3 documentation. Portal labels and access policies can
change.

## Quick answer

- `npdb convert bagel midrc` accepts a **Discovery dataset URL or ID**,
  a **file GUID**, or a path to a local JSON manifest. Patient, case,
  imaging-study, and DICOM series identifiers are not file GUIDs.
- Find file GUIDs in **Exploration > Data Files > Object Id** at
  <https://data.midrc.org/explorer>. Copy the complete value, including the
  `dg.MD1R/` prefix.
- Create a download manifest by selecting a cohort in Exploration and using
  **Download file manifest**, not **Download table**. While signed out, the
  live portal labels these buttons **Login to download file manifest** and
  **Login to download table**.
- MIDRC calls its data open access, but its resources are open to
  **registered users under a Data Use Agreement (DUA)**. Anonymous discovery
  is not the same as anonymous file download. Use an authenticated Gen3
  client for downloads.

> [!WARNING]
> MIDRC preparation requires `dcm2niix` on `PATH` for DICOM imaging and
> linked structured metadata. Currently structural MR and linked NIfTI
> derivatives are supported; unresolved images and unsupported formats are
> retained outside the prepared dataset with diagnostic reports. See
> [Dataset conversion and caching](#dataset-conversion-and-caching).

## What is a GUID, and where do I find one?

Gen3 assigns a globally unique identifier (GUID) to an uploaded file. The
same file identifier is called:

| Context | Field or label |
|---------|----------------|
| MIDRC Exploration, Data Files tab | `Object Id` |
| Gen3 structured file metadata / download manifest | `object_id` |
| Gen3 file index (indexd) | `did` |
| Gen3 single-file download command | `--guid` |

For example, this real MIDRC index record was publicly readable on the
verification date:

```text
dg.MD1R/00001e99-cdd5-43d1-871a-09b6f9df5dad
```

It identifies a ZIP file, not the entire `Open-R1` project. The record can
be inspected at
<https://data.midrc.org/index/index/dg.MD1R/00001e99-cdd5-43d1-871a-09b6f9df5dad>.
This example demonstrates the identifier format; it is not a promise that
the file will remain available or suitable for Neurobagel conversion.

To find an identifier for your own selection:

1. Open [MIDRC Exploration](https://data.midrc.org/explorer).
2. Apply the filters for the cases, studies, or files you need.
3. Select the **Data Files** tab.
4. Find the **Object Id** column in the wide results table; you may need to
   scroll horizontally.
5. Copy the complete identifier, or click its link to open the file page.

Do not substitute the **Patient ID**, **Case ID**, **Study UID**,
**Series Uid**, **Submitter Id**, or a metadata node's `id`. Those describe
different entities. A GUID is supplied by MIDRC; you do not invent one.

## Where do I create the manifest?

A download manifest is a local JSON list of file references generated from
your portal selection. It is not an upload form, credentials file, or
pre-existing dataset manifest you need to locate elsewhere on the website.

1. Go to [MIDRC Login](https://data.midrc.org/login). On the verification
   date, MIDRC offered **InCommon Login** and **ORCID Login**.
2. Complete registration and accept the applicable DUA when prompted.
   Consult [MIDRC's DUA information](https://www.midrc.org/midrc-data-use-agreement);
   the current agreements are presented by the portal during registration.
3. Open **Exploration** and filter down to the cohort you want.
4. For an explicit file selection, use the **Data Files** tab and
   **Download file manifest**. The **Cases** tab also exposes a
   **Download file manifest for cases** action for the associated files.
5. If the portal says **cohort too large; make a smaller selection**, narrow
   the filters before exporting.
6. Save the downloaded JSON file locally, for example as
   `midrc-manifest.json`.

**Download table** exports metadata, not the file-download manifest. Keep
the metadata export too if you need clinical information; the download
manifest is primarily a list of files, not a phenotype table.

The signed-out buttons and tab/column names above were observed in the
live portal. An authenticated export was not performed during verification.
The export workflow and standard manifest format are supported by the
[official Gen3 client documentation](https://docs.gen3.org/gen3-resources/tools/data-client/#multiple-file-download-with-manifest).

### Supported manifest formats

The Gen3 client's standard manifest is a **top-level JSON array**, with
file identifiers under `object_id`. A minimal illustrative entry is:

```json
[
  {
    "object_id": "dg.MD1R/00001e99-cdd5-43d1-871a-09b6f9df5dad"
  }
]
```

Portal exports can also contain `file_name`, `file_size`, and `subject_id`.
The official client's [manifest definition](https://github.com/uc-cdis/cdis-data-client/blob/master/gen3-client/g3cmd/utils.go)
and [manifest reader](https://github.com/uc-cdis/cdis-data-client/blob/master/gen3-client/g3cmd/download-multiple.go)
confirm these fields and the array format. Use the exported manifest
unchanged with `gen3-client`.

The [npdb provider](../../../../src/npdb/managers/midrc.py) accepts this
standard format unchanged. For backward compatibility it also accepts a
**JSON object** containing `records` or `files`, with identifiers under
`object_id`, `guid`, or `did`:

```json
{
  "records": [
    {
      "did": "dg.MD1R/00001e99-cdd5-43d1-871a-09b6f9df5dad"
    }
  ]
}
```

`{"files": [{"guid": "..."}]}` is another accepted shape. Duplicate GUIDs
are downloaded once. Empty manifests, malformed entries, and missing identifiers
fail explicitly rather than being silently skipped.

## Can files be downloaded without authentication?

**Do not assume so for MIDRC.** The [MIDRC overview](https://www.midrc.org)
states that resources are open to registered users according to the DUA.
The word "Open" in a project name or storage bucket is not proof of
anonymous file access.

Anonymous checks for the example GUID above produced:

| Operation | Observed result |
|-----------|-----------------|
| Browse Exploration and the Data Files table | Works without login |
| Read `/index/index/<GUID>` | Returns file metadata, including `did` and storage location |
| Export a manifest through the portal | Button says `Login to download file manifest` |
| Request `/user/data/download/<GUID>` without credentials | HTTP **401 Unauthorized** |
| Send an unsigned HEAD request to the indexed S3 object's HTTPS URL | HTTP **403 Forbidden** |

The index record points to `s3://open-data-midrc/zip/...`, with authorization
resource `/programs/Open/projects/R1`. A readable index record does not grant
access to the bytes.

These checks did not download imaging data or use an account. They establish
that anonymous downloading is not available through the tested route for
this file, not that every file or external mirror has the same policy.
Gen3 can support anonymous data in other deployments; that does not imply
MIDRC enables it for a particular file.

## Supported authenticated download workflow

Install the official [Gen3 Data Client](https://docs.gen3.org/gen3-resources/tools/data-client/)
following its installation instructions. Then:

1. Sign in to MIDRC and open **Profile**.
2. Select **Create API key**, then **Download json** to save
   `credentials.json`. Treat it as a password: do not commit it or share it.
3. Configure a profile and download using the file GUID or portal manifest:

```bash
gen3-client configure \
  --profile=midrc \
  --cred=/absolute/path/to/credentials.json \
  --apiendpoint=https://data.midrc.org

gen3-client download-single \
  --profile=midrc \
  --guid=dg.MD1R/00001e99-cdd5-43d1-871a-09b6f9df5dad \
  --download-path=./midrc-downloads

gen3-client download-multiple \
  --profile=midrc \
  --manifest=./midrc-manifest.json \
  --download-path=./midrc-downloads
```

Choose either download command as appropriate. These are documented command
examples, not commands executed during verification. The client obtains
authorized download URLs rather than treating an `s3://` index location as
an ordinary HTTP URL.

For access or registration problems, contact
[midrc-support@gen3.org](mailto:midrc-support@gen3.org).

## Dataset conversion and caching

Install the [native tools](../../native_tools.md), including `dcm2niix` and the
Rust-only BIDS validator, then the MIDRC dependency group (`uv sync --group midrc`) and obtain
credentials from your MIDRC Profile. These inputs use the same command:

```bash
npdb convert bagel midrc '<file-guid>' <output-directory>
npdb convert bagel midrc <manifest.json> <output-directory>
npdb convert bagel midrc H6K0-A61V ./output \
  --credentials-path /absolute/path/to/credentials.json \
  --cache-dir /absolute/path/to/midrc-cache
```

The complete Discovery URL, such as
<https://data.midrc.org/discovery/H6K0-A61V/>, is also accepted. It must belong
to the configured endpoint (`NP_MIDRC_ENDPOINT`, default
`https://data.midrc.org`). The provider reads
`/mds/metadata/<dataset-id>` and resolves every GUID in
`gen3_discovery.data_download_links`. It does not infer the download list
from `files_count`; the Duke example has `files_count=1` but four download links.
Datasets without such links require an Exploration manifest instead.

Credentials are required through `--credentials-path` or
`NP_MIDRC_CREDENTIALS`. The Gen3 Python SDK obtains signed HTTPS URLs, and
files are streamed to disk rather than loaded into memory. Indexed nested
filenames are preserved, with unsafe paths and conflicting destinations rejected.
The CLI reports file counts/bytes and transfer progress. Disk space is checked
for downloads and staging before transfer; extraction requires additional space.
Size and a supported checksum (SHA-256, SHA-1, or MD5 when available) are checked
before a download is marked complete. Ordinary authorization failures stop the
download; a recognized expired storage URL is renewed once.

Discovery inputs require `--cache-dir` or `NP_NPDB_CACHE_DIR`. A validated
cached file is reused on retry, with separate staged copies under the output
directory. MIDRC role-isolated extraction retains both staged archives and cached
originals. Cache and fetch directories must not overlap. Failed transfers do
not leave a completed file; interrupted `.part`
files must be removed before retrying. Byte-range resume is not implemented.
Allow enough space for cache, staged archives, and extracted content.

For Duke CSpineSeg, the four indexed ZIPs total **13,561,953,232 bytes**
(about **13.56 GB compressed**). This is an example of generic Discovery
resolution, not a special conversion mechanism. The
[publication](https://pmc.ncbi.nlm.nih.gov/articles/PMC12559328/) describes
original DICOM series in **MRI Image Files**, converted MRI NIfTIs in
**Annotation Files**, masks in **Segmentation Files**, and clinical TSVs in
**Structured Data TSVs**. Do not infer file roles solely from portal labels.

Providers now own their unpacking and preparation steps:

```text
provider.fetch -> provider.unpack -> provider.prepare -> local2bagel
```

The default preparation retains existing participant tables, flattens
`rawdata`, and reorganizes auxiliary directories. MIDRC specializes that flow:

1. Extract `<dataset>_structured.zip` first into isolated
  `sourcedata/midrc/structured`. Read exported node types and explicit case,
  study, series, and annotation foreign keys. Build `participants.tsv` and its
  sidecar using the existing participant standardization utilities.
2. Extract `<dataset>_imaging_files.zip` separately. Unpack nested series ZIPs,
  verify DICOM study/series UIDs against metadata, and convert each mapped series
  with `dcm2niix` (compressed NIfTI plus anonymized JSON sidecars).
3. Extract other archives into isolated role directories and place linked NIfTI
  images under `<dataset>/derivatives/<archive-suffix>/sub-*/ses-*/anat`.
  Discrete `_SEG` images use `desc-SEG_dseg`; original image bytes are preserved.

Prepared data is returned at `<fetch-directory>/<dataset>`. Source archives,
TSVs, intermediate conversions, and reports remain outside that validated tree.
For example, `593973-000001_Study-MR-1_Series-22.nii.gz` becomes
`sub-593973000001_ses-MR1_run-22_T2w.nii.gz` when the linked series metadata
establishes T2-weighting. Filename numbers are retained as labels, not treated
as study/series UIDs. Derivative sidecars retain `OriginalFileName`, annotation
metadata, and `Sources` links. Anatomical derivative skull-stripping status is
accepted from explicit metadata or established as false by voxel/affine
equivalence to raw; otherwise the image is excluded rather than guessed.

`sourcedata/midrc/conversion_report.json` and `derivative_report.json` list
mapped outputs and quarantined sources with reasons. Quarantined files remain
in their source directories, not in the BIDS tree. Unknown acquisitions,
unmapped series, unsupported annotation formats (including DICOM-SEG), and
ambiguous converter outputs are reported. Conversion errors, unsafe extraction,
conflicting metadata, and output collisions fail explicitly. Empty imaging
conversion is not reported as success.

Offline preparation is available through provider methods without credentials
or network access:

```python
from npdb.managers.midrc import MIDRCProviderManager

provider = MIDRCProviderManager()
staged = provider.unpack("/path/to/local/archive-directory", output_dir="/path/to/new/staging")
bids = provider.prepare(staged)
```

Completed extraction and preparation are reused on retry when the archive
manifest matches and all reported images, sidecars, and dataset metadata remain
present. This lets annotation/export be retried with different automation
options without extracting or converting again. Generated series ZIPs in
sourcedata are not treated as downloaded dataset archives. Incomplete outputs
are not overwritten; use a fresh staging destination for an interrupted
preparation. Keep cache and staging separate. Archives are preferred
when an earlier flat extraction has lost derivative-role provenance. Check raw
and derivative roots with `bids-validator-rust`, installed by
`bash scripts/install_bids_validator.sh`. Its pinned Rust `bids-validate 0.0.3`
structural checks cover dataset
descriptions and image naming, but do not establish full sidecar or NIfTI
semantic compliance. Recommended metadata warnings are not suppressed, and
absent license/author information is not invented.

For incompatible layouts, prepare a suitable local dataset first and follow
the [local ingestion guide](../../ingestion.md). A successful Discovery
resolution/download is not proof that the source is ready for Neurobagel.

## Official references

- [MIDRC overview and registered-user access](https://www.midrc.org)
- [MIDRC login](https://data.midrc.org/login) and [Exploration](https://data.midrc.org/explorer)
- [MIDRC Data Use Agreements](https://www.midrc.org/midrc-data-use-agreement)
- [Gen3 portal guide: Profile, Exploration, and GUID links](https://docs.gen3.org/gen3-resources/user-guide/portal/)
- [Gen3 client guide: credentials, GUIDs, and manifests](https://docs.gen3.org/gen3-resources/tools/data-client/)
- [Gen3 authentication and authorization](https://docs.gen3.org/gen3-resources/user-guide/access-data/)

# Chapter 3 — BIDS Metadata for Search

*Reading time: ~10 minutes*

---

## What Is BIDS?

The **Brain Imaging Data Structure** (BIDS, v1.11.0) is a community standard for
organizing neuroimaging datasets. Before BIDS, every lab had its own naming
conventions, directory layouts, and metadata formats. Finding data across
studies — or even within one's own lab — was painful.

BIDS solves this by defining:

1. **A strict directory hierarchy** (`sub-XX/[ses-XX/]<datatype>/`)
2. **A filename convention** using hyphen-separated entity key-value pairs
3. **Standardized JSON sidecar metadata** alongside each imaging file
4. **Tabular demographic data** (`participants.tsv`)

The result: any BIDS dataset is immediately machine-readable. This is exactly
what makes it ideal input for an ElasticSearch indexing pipeline.

**Spec:** https://bids-specification.readthedocs.io/en/stable/

---

## Directory Structure

Our example dataset (`ds001`) looks like this:

```
ds001/
├── dataset_description.json        ← dataset-level metadata (REQUIRED)
├── participants.tsv                ← subject demographics
├── task-balloonanalogrisktask_bold.json  ← top-level sidecar (inheritable)
├── sub-01/
│   ├── anat/
│   │   ├── sub-01_T1w.nii.gz      ← structural MRI image
│   │   └── sub-01_T1w.json        ← sidecar: TR, TE, flip angle, etc.
│   └── func/
│       ├── sub-01_task-balloonanalogrisktask_run-01_bold.nii.gz
│       ├── sub-01_task-balloonanalogrisktask_run-01_bold.json
│       ├── sub-01_task-balloonanalogrisktask_run-01_events.tsv
│       ├── sub-01_task-balloonanalogrisktask_run-02_bold.nii.gz
│       ├── sub-01_task-balloonanalogrisktask_run-02_bold.json
│       ├── sub-01_task-balloonanalogrisktask_run-02_events.tsv
│       └── ...
├── sub-02/
│   └── ...
└── ...
```

Key points:
- **One directory per subject** (`sub-01/`, `sub-02/`, ...)
- **Data type subdirectories**: `anat/` (structural), `func/` (functional),
  `dwi/` (diffusion), `fmap/` (field maps), etc.
- **Every NIfTI file has a JSON sidecar** with acquisition metadata
- **No sessions** in this dataset (single session = no `ses-XX/` level)

---

## Filename Anatomy

```
sub-01_task-balloonanalogrisktask_run-01_bold.nii.gz
│      │                          │        │    │
│      │                          │        │    └── extension
│      │                          │        └── suffix (modality/contrast)
│      │                          └── entity: run index
│      └── entity: task label
└── entity: subject label
```

**Entities** are key-value pairs separated by hyphens, and entities are separated
from each other by underscores. The full list includes: `sub`, `ses`, `task`,
`acq`, `ce`, `rec`, `dir`, `run`, `echo`, `flip`, `inv`, `part`, and others.
The order is strictly defined by the
[entity table](https://bids-specification.readthedocs.io/en/stable/appendices/entity-table.html).

For our search engine, we extract entities from the filename as **keyword
fields** in ES — enabling exact filtering by subject, task, run, etc.

---

## JSON Sidecar Metadata

This is the richest source of searchable metadata. A typical `_bold.json`:

```json
{
  "TaskName": "balloon analog risk task",
  "RepetitionTime": 2.0,
  "EchoTime": 0.025,
  "FlipAngle": 70,
  "MagneticFieldStrength": 3,
  "Manufacturer": "Siemens",
  "ManufacturersModelName": "TrioTim",
  "InstitutionName": "Carnegie Mellon University",
  "SliceThickness": 4,
  "PhaseEncodingDirection": "j-"
}
```

The fields we'll index (and their ES types):

| BIDS Field | ES Field Type | Why |
|-----------|---------------|-----|
| `RepetitionTime` | `float` | Range queries: find scans with TR between 1-3s |
| `EchoTime` | `float` | Range queries |
| `FlipAngle` | `float` | Range queries |
| `MagneticFieldStrength` | `float` | Filter: 1.5T vs 3T vs 7T |
| `Manufacturer` | `keyword` | Exact match: "Siemens", "GE", "Philips" |
| `ManufacturersModelName` | `keyword` | Exact match: "Prisma", "TrioTim" |
| `InstitutionName` | `keyword` | Exact match: filter by site |
| `TaskName` | `text` | Full-text search on task descriptions |
| `SeriesDescription` | `text` | Full-text search |

---

## The Inheritance Principle

BIDS has an elegant mechanism to avoid metadata duplication. A JSON sidecar
placed at a higher directory level **applies to all matching files below it**,
unless overridden at a lower level:

```
ds001/
├── task-balloonanalogrisktask_bold.json       ← applies to ALL bold files
├── sub-01/
│   └── func/
│       ├── sub-01_task-balloonanalogrisktask_run-01_bold.json  ← overrides
│       └── sub-01_task-balloonanalogrisktask_run-01_bold.nii.gz
```

If the root-level JSON says `"RepetitionTime": 2.0` and the file-level JSON
doesn't mention `RepetitionTime`, then `RepetitionTime = 2.0` still applies —
it's **inherited**.

If the file-level JSON says `"RepetitionTime": 3.0`, it **overrides** the
inherited value for that specific file.

**This is why we use `pybids`** — its `BIDSLayout.get_metadata()` method
resolves the inheritance chain automatically. Manually parsing JSON sidecars
would require reimplementing this logic.

---

## participants.tsv

Subject-level demographics, one row per subject:

```tsv
participant_id	sex	age
sub-01	M	24
sub-02	F	31
sub-03	M	28
...
```

We'll merge these into each document during ingestion, so you can filter scans
by `age` or `sex` in ES.

---

## DICOM: The Upstream Source

The raw data from MRI scanners comes in **DICOM** format — a standard that
predates BIDS by decades. Each DICOM file contains a binary header with hundreds
of tag-value pairs (e.g., `(0018,0080) = RepetitionTime`) plus the image
pixel data.

The pipeline from scanner to BIDS:

```
Scanner → DICOM files → dcm2niix → NIfTI (.nii.gz) + JSON sidecars → BIDS dataset
```

[**dcm2niix**](https://github.com/rordenlab/dcm2niix) is the gold standard
converter. It extracts metadata from DICOM headers and writes it into
BIDS-compatible JSON sidecars. Key things it handles:

- **Unit conversion**: DICOM stores times in milliseconds; BIDS requires
  seconds. dcm2niix divides by 1000.
- **De-identification**: Patient name/ID from DICOM are excluded from the JSON
  sidecars. (You must still be careful with `sourcedata/` if you keep raw DICOMs.)

For this course, we work directly with the **already-converted BIDS dataset**.
The DICOM→BIDS conversion is a solved problem; our focus is what happens *after*
— getting that metadata into a searchable database.

---

## pybids: Programmatic Access

[`pybids`](https://bids-standard.github.io/pybids/) provides a `BIDSLayout`
object that indexes the entire dataset and exposes a query API:

```python
from bids import BIDSLayout

layout = BIDSLayout("data/ds001")

# List all subjects
layout.get_subjects()  # ['01', '02', '03', ...]

# Get all BOLD files
bold_files = layout.get(suffix="bold", extension=".nii.gz")

# Get resolved metadata for one file (handles inheritance)
meta = layout.get_metadata(bold_files[0].path)
# {'TaskName': 'balloon analog risk task', 'RepetitionTime': 2.0, ...}
```

We'll use this in Notebook 1 to iterate over every file and extract its full
metadata for indexing.

---

## Key Reference Links

- [BIDS Specification](https://bids-specification.readthedocs.io/en/stable/) — The definitive reference
- [BIDS Common Principles](https://bids-specification.readthedocs.io/en/stable/common-principles.html) — Inheritance, entities, file formats
- [MRI-specific metadata](https://bids-specification.readthedocs.io/en/stable/modality-specific-files/magnetic-resonance-imaging-data.html) — All MRI sidecar fields
- [bids-examples on GitHub](https://github.com/bids-standard/bids-examples) — Example datasets
- [pybids documentation](https://bids-standard.github.io/pybids/) — Python API
- [dcm2niix](https://github.com/rordenlab/dcm2niix) — DICOM to NIfTI converter

---

**Next:** Proceed to **[Notebook 1: Setup & Ingest Pipeline](../notebooks/01-setup-and-ingest.ipynb)**.
After completing Notebook 1, read [Chapter 4: Kibana Dashboards](04-kibana-dashboards.md)
before starting Notebook 4.

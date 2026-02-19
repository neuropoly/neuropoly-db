# Neuroimaging Metadata Search Engine — Course Overview

## What We're Building

By the end of this course you will have a fully working **searchable neuroimaging
metadata database** powered by ElasticSearch and Kibana. The system indexes BIDS
dataset metadata (JSON sidecars, participant information) and supports three
search paradigms:

1. **Keyword search** — Find scans by exact metadata values (manufacturer,
   field strength, modality).
2. **Vector similarity search** — Find scans whose metadata is *semantically
   similar* to a natural language query, using dense vector embeddings.
3. **Hybrid search** — Combine both for the best of precision and recall.

The architecture:

```
BIDS Dataset (NIfTI + JSON sidecars)
        │
        ▼
  Python ingestion script
  (pybids + sentence-transformers + elasticsearch-py)
        │
        ▼
  ┌──────────────────────┐
  │   ElasticSearch 9.3   │ ◄── keyword + kNN vector search
  └──────────────────────┘
        │
        ▼
  ┌──────────────────────┐
  │     Kibana 9.3        │ ◄── Discover, dashboards, Dev Tools
  └──────────────────────┘
```

---

## Course Structure

| # | Material | Format | Time |
|---|----------|--------|------|
| 0 | **Overview & Setup** (this document) | Markdown | ~20 min |
| 1 | **Vector Database Concepts** | Markdown | ~15 min |
| 2 | **ElasticSearch Architecture & Vector Search** | Markdown | ~15 min |
| 3 | **BIDS Metadata for Search** | Markdown | ~10 min |
| — | **Notebook 1: Setup & Ingest Pipeline** | Jupyter | ~40 min |
| — | **Notebook 2: Keyword & Filtered Search** | Jupyter | ~30 min |
| — | **Notebook 3: Vector & Hybrid Search** | Jupyter | ~35 min |
| 4 | **Kibana Dashboards** | Markdown | ~5 min |
| — | **Notebook 4: Kibana Exploration** | Jupyter | ~25 min |
| 5 | **Next Steps** | Markdown | ~5 min |
| | | **Total** | **~3 hours** |

**Reading flow:** Read each Markdown chapter first, then complete the
corresponding notebook(s) before moving on.

---

## Prerequisites

You should be comfortable with:

- **Python 3.10+** — scripting, pip/venv, working with JSON and pandas.
- **Docker & Docker Compose** — running containers, reading compose files.
- **REST APIs** — HTTP methods (GET/POST/PUT), JSON request/response bodies.
- **Terminal/CLI** — standard shell commands.
- **Neuroimaging** — what an MRI scan is, what DICOM and NIfTI files represent,
  general awareness of metadata like TR, TE, flip angle, field strength.

No prior ElasticSearch experience is required (minor exposure is fine).

---

## Environment Setup

### 1. Clone / enter the project

```bash
cd /path/to/neuropoly-db
```

### 2. Set the kernel memory map limit (required for ES)

ElasticSearch uses memory-mapped files extensively. The default Linux limit is
too low.

```bash
# Temporary (until reboot):
sudo sysctl -w vm.max_map_count=262144

# Permanent (add to /etc/sysctl.conf):
echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
```

### 3. Start ElasticSearch + Kibana

```bash
docker compose up -d
```

Wait ~30–60 seconds, then verify:

```bash
# ElasticSearch health
curl -s http://localhost:9200/_cluster/health | python3 -m json.tool

# Expected: "status": "green" (or "yellow" for single-node)
```

Kibana will be available at **http://localhost:5601** once it connects to ES
(may take an extra 30s after ES is healthy).

### 4. Create a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 5. Download the BIDS dataset

```bash
bash scripts/download_dataset.sh
```

This downloads **ds001** from the
[bids-examples](https://github.com/bids-standard/bids-examples) repository — a
small, well-known BIDS-compliant dataset with ~16 subjects performing a
"balloon analog risk task" in the scanner. It includes:

- Anatomical T1w scans
- Functional BOLD scans with task events
- JSON sidecar metadata for every image
- `participants.tsv` with demographics

### 6. Verify everything

```bash
# ES is up
curl -s http://localhost:9200 | grep "name"

# Dataset is present
ls data/ds001/sub-01/
```

You're ready. Proceed to **[Chapter 1: Vector Database Concepts](01-vector-db-concepts.md)**.

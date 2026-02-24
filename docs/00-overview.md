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
| 1 | **Vector Database Concepts** | Markdown | ~25 min |
| 2 | **ElasticSearch Architecture & Vector Search** | Markdown | ~20 min |
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

### Knowledge

You should be comfortable with:

- **Python 3.10+** — scripting, pip/venv, working with JSON and pandas.
- **Docker & Docker Compose** — running containers, reading compose files.
- **REST APIs** — HTTP methods (GET/POST/PUT), JSON request/response bodies.
- **Terminal/CLI** — standard shell commands.
- **Neuroimaging** — what an MRI scan is, what DICOM and NIfTI files represent,
  general awareness of metadata like TR, TE, flip angle, field strength.

No prior ElasticSearch experience is required (minor exposure is fine).

### System Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| **Free disk space** | **20 GB** | 40+ GB |
| RAM | 4 GB | 8+ GB |
| Docker Engine | 24.0+ | latest |
| Docker Compose | v2+ | latest |

### Disk Space — Read This Before You Begin

This course installs a non-trivial stack. Here's what goes on your disk:

| Component | Size |
|-----------|------|
| ElasticSearch 9.3 Docker image | ~1.4 GB |
| Kibana 9.3 Docker image | ~1.4 GB |
| Python venv (PyTorch, CUDA libs, sentence-transformers, etc.) | ~8 GB |
| Embedding model download (`all-MiniLM-L6-v2`) | ~90 MB |
| BIDS datasets (all bids-examples, ~80 datasets) | ~60 MB |
| ES index data (after ingestion) | < 10 MB |
| **Total** | **~11 GB** |

The bulk of the space (~6 GB) goes to PyTorch and NVIDIA CUDA libraries that
`sentence-transformers` pulls in. The neuroimaging datasets and ES index are
small by comparison.

But here's the catch: ElasticSearch has built-in **disk watermark thresholds**
that block shard allocation when the disk gets full:

| Watermark | Default | What happens |
|-----------|---------|--------------|
| Low | 85% used | ES stops allocating new shards to the node |
| High | 90% used | ES tries to relocate shards *away* from the node |
| Flood stage | 95% used | All indices go **read-only** |

On a dev laptop this matters a lot. If your disk crosses 85% after
installing everything, ES will silently refuse to create indexes — the Python
client just hangs until it times out. The cluster goes RED and nothing works
until you fix it.

**Check your free space now:**

```bash
df -h /
```

Read the result against these tiers:

---

**🟢 40+ GB free — You're in great shape.**
The course will use ~11 GB, and you'll still have plenty of headroom to
experiment, create extra indexes, and play around without ever hitting
ElasticSearch's watermarks. No special steps needed — carry on.

---

**🟡 20–40 GB free — Enough to complete the course, but it's tight.**
You'll have room for the baseline installation and all the notebook exercises.
However, if you start experimenting heavily (creating lots of extra indices,
downloading more datasets), you could tip past the 85% watermark and ES will
start refusing to allocate shards.

Keep an eye on `df -h /` as you go. If you hit trouble, see
[Appendix: Disabling ES Disk Watermarks](#appendix-disabling-es-disk-watermarks)
at the bottom of this page.

---

**🔴 Less than 20 GB free — Not enough for the course as-is.**
You need to free up space first. Some ideas:

- Move large files (datasets, VMs, container images) to an external drive
- Run `docker system prune` to reclaim space from unused Docker objects
- Clear pip / conda caches: `pip cache purge` or `conda clean --all`
- Check for big offenders: `du -sh ~/* | sort -rh | head -20`

If you absolutely cannot free more space, the course *may* still work if you
disable ES watermarks outright — but you're running the risk of filling your
system drive, which on Linux can lock you out of your desktop environment and
require CLI recovery. Only do this if you understand the risk. See
[Appendix: Disabling ES Disk Watermarks](#appendix-disabling-es-disk-watermarks)
below.

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

If you see `"status": "red"`, check the
[Appendix: Disabling ES Disk Watermarks](#appendix-disabling-es-disk-watermarks)
section — your disk is likely past the 85% watermark threshold.

Kibana will be available at **http://localhost:5601** once it connects to ES
(may take an extra 30s after ES is healthy).

### 4. Create a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 5. Download the BIDS datasets

```bash
bash scripts/download_dataset.sh
```

This shallow-clones the entire
[bids-examples](https://github.com/bids-standard/bids-examples) repository
and copies every valid BIDS dataset (those with `dataset_description.json` and
at least one NIfTI file) into `data/`. The result is **~80 datasets** covering
a wide range of modalities, scanners, field strengths (1.5 T, 3 T, 7 T), and
institutions — roughly 4,000+ NIfTI files in ~60 MB of dummy data.

Highlights among the datasets:

| Dataset | What's in it |
|---------|--------------|
| **7t_trt** | 7 T test-retest (largest dataset by file count) |
| **ds000117** | Multi-modal anat+func+dwi+fmap+meg, Siemens TrioTim 3 T |
| **ds001** | Balloon analog risk task, sparse metadata baseline |
| **eeg_rest_fmri** | EEG + fMRI resting state at 1.5 T |
| many others… | DWI, FLAIR, FLASH, PET, ASL, and more |

Having dozens of datasets with different scanners, field strengths, and
metadata richness makes the search demos in Notebooks 2–3 far more
meaningful — you'll see real score variation instead of identical results.

### 6. Verify everything

```bash
# ES is up
curl -s http://localhost:9200 | grep "name"

# Datasets are present (should show ~80 directories)
ls data/ | head -20
```

You're ready. Proceed to **[Chapter 1: Vector Database Concepts](01-vector-db-concepts.md)**.

---

## Appendix: Disabling ES Disk Watermarks

If ElasticSearch refuses to create indexes (the Python client hangs on
`client.indices.create(...)` and eventually raises `ConnectionTimeout`), the
cluster is likely blocking shard allocation because your disk crossed the 85%
watermark.

**How to tell for sure:**

```bash
# Cluster status — "red" with unassigned shards means watermark trouble
curl -s http://localhost:9200/_cluster/health?pretty | grep -E "status|unassigned"

# Confirm from container logs
docker logs es-neuroimaging 2>&1 | grep -i "watermark" | tail -3
```

If you see `"high disk watermark [90%] exceeded"`, that's the problem.

### Option A: Free disk space (recommended)

The safest fix is to reclaim space so your disk drops below 85% used:

```bash
# See current usage
df -h /

# Common space reclaimers:
docker system prune            # unused images, containers, build cache
pip cache purge                # pip download cache
sudo apt clean                 # apt package cache (Debian/Ubuntu)
du -sh ~/* | sort -rh | head   # find the big offenders
```

After freeing space, delete the broken index and recreate it:

```bash
curl -X DELETE "http://localhost:9200/neuroimaging"
# Then re-run the index creation cell in Notebook 1
```

### Option B: Disable the watermark checks

You can tell ES to ignore disk usage and allocate shards regardless. This works
instantly, no restart required:

```bash
curl -X PUT "http://localhost:9200/_cluster/settings" \
  -H 'Content-Type: application/json' -d '{
    "persistent": {
      "cluster.routing.allocation.disk.threshold_enabled": false
    }
  }'
```

Then delete the broken index and recreate it:

```bash
curl -X DELETE "http://localhost:9200/neuroimaging"
# Then re-run the index creation cell in Notebook 1
```

> **⚠️ Understand the risk.** With watermarks disabled, ES will happily
> consume disk space until there's nothing left. On Linux, if your root
> partition fills to 100%, the desktop environment may fail to start,
> and you'll need CLI recovery to free space. This is not hypothetical —
> it's a real pain.
>
> If you go this route:
> - Keep `df -h /` handy and check it as you experiment.
> - When you're done with the course, shut down the cluster:
>   `docker compose down` (or `docker compose down -v` to reclaim the
>   ES data volume too).
> - Don't leave ES running unattended with watermarks off.

To **re-enable** watermarks later (e.g., if you repurpose this cluster for
real work):

```bash
curl -X PUT "http://localhost:9200/_cluster/settings" \
  -H 'Content-Type: application/json' -d '{
    "persistent": {
      "cluster.routing.allocation.disk.threshold_enabled": null
    }
  }'
```

Setting it to `null` restores the ES defaults (85% / 90% / 95%).

# NeuroPoly DB — Copilot Instructions

## Project Summary
Neuroimaging metadata search engine: BIDS datasets → Elasticsearch → vector/keyword/hybrid search.

## Stack
| Component | Details |
|-----------|---------|
| **Elasticsearch 9.3** | `http://localhost:9200` — start: `docker-compose up -d` |
| **Kibana 9.3** | `http://localhost:5601` |
| **Python 3.12** | venv at `.venv/`. Install: `pip install -r requirements.txt` |
| **Jupyter notebooks** | `notebooks/01–07` — sequential, each builds on the previous |
| **sentence-transformers** | `all-mpnet-base-v2` (768d dense), SPLADE (sparse) |
| **PyBIDS + nibabel + pydicom** | BIDS/neuroimaging data parsing |
| **pandas, numpy, tqdm, matplotlib** | Data processing and visualization |

## Project Layout
```
data/              # BIDS datasets (neuroimaging data, atlases, derivatives)
notebooks/         # 01-setup-and-ingest → 07-poc2-splade-rrf-benchmark
scripts/
  ingest.py        # Main BIDS → Elasticsearch ingestion pipeline
docker-compose.yml # ES 9.3 + Kibana 9.3
requirements.txt   # Python deps
poc1_results.json  # Benchmark results from notebook 06
.github/
  agents/          # Custom Copilot agents
  instructions/    # File-specific instructions
  prompts/         # Reusable prompt templates
```

## Elasticsearch Conventions
- **ES host**: `http://localhost:9200` (notebook default; override with `ES_HOST` env var)
- **Indices**: `neuroimaging` (base), `neuroimaging-poc1` (768d mpnet), `neuroimaging-poc2` (SPLADE)
- **Dense vector field**: `metadata_embedding` — `dense_vector`, 768d, cosine, `int8_hnsw`
- **Text field**: `description_text` — assembled BIDS metadata, used for BM25
- **Key fields**: `dataset`, `suffix`, `Manufacturer`, `MagneticFieldStrength`, `task`, `TaskName`, `RepetitionTime`, `EchoTime`
- BM25 query: `{"match": {"description_text": expanded_query}}`
- kNN query: `{"knn": {"field": "metadata_embedding", "query_vector": vec, "k": k, "num_candidates": k*20}}`
- Hybrid: two separate requests → Python `rrf_fuse()` with k=60 (never use ES native RRF — requires paid license)
- Bulk ingest: `helpers.bulk(client, actions, chunk_size=200)` → `client.indices.refresh()`

## Python Conventions
- Encoder: `SentenceTransformer("all-mpnet-base-v2", device="cpu")`; set `os.environ['CUDA_VISIBLE_DEVICES'] = ''`
- Always validate: `assert client.ping(), f"Cannot reach ES at {ES_HOST}"`; set `request_timeout=120`
- Display results: `display(pd.DataFrame(rows))` not `print()`
- Suppress noisy warnings: `warnings.filterwarnings('ignore', category=FutureWarning)`
- BIDS parsing: `bids.BIDSLayout(dataset_path, validate=False)` from PyBIDS

## BIDS Data
- Datasets in `data/<name>/` with `dataset_description.json`, `participants.tsv`, `sub-XX/` folders
- Scanner metadata in JSON sidecars beside `.nii.gz` files
- Key suffixes: `bold`, `T1w`, `T2w`, `dwi`, `phasediff`, `epi`, `UNIT1`
- Key metadata fields assembled into `description_text` for BM25 search

## Development Notes
- Run notebook 01 first to create the base `neuroimaging` index
- Benchmark: use `GOLD_QUERIES` in notebook 06; key metrics are **P@5** and **MRR**
- Results saved to `poc1_results.json`; compared in notebook 07
- Do not use `CUDA` — all encoding is CPU-only in this project

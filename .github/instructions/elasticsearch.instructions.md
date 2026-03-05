---
applyTo: "**/scripts/**, **/notebooks/**"
---

# Elasticsearch 9.3 Conventions — neuropoly-db

## Client Setup
```python
from elasticsearch import Elasticsearch, helpers
client = Elasticsearch("http://localhost:9200", request_timeout=120)
assert client.ping(), "Cannot reach ES"
print(f"Connected to ES {client.info()['version']['number']}")
```

## Index Mappings
```python
# Dense vector field — always use int8_hnsw for memory efficiency
"metadata_embedding": {
    "type": "dense_vector",
    "dims": 768,
    "similarity": "cosine",
    "index_options": {"type": "int8_hnsw"}
}
# Text for BM25
"description_text": {"type": "text"}
# Exact match / filter fields
"suffix": {"type": "keyword"}
# Always set for local single-node dev
settings={"number_of_replicas": 0}
```

## Query Patterns
```python
# BM25
resp = client.search(index=idx, query={"match": {"description_text": expanded_query}}, size=k)

# kNN — always set num_candidates = k * 20
resp = client.search(index=idx, knn={
    "field": "metadata_embedding",
    "query_vector": embed(expanded_query),
    "k": k,
    "num_candidates": k * 20
})

# Hybrid: two separate requests + client-side RRF (free, no license needed)
bm25_hits = client.search(index=idx, query={"match": ...}, size=k*3)["hits"]["hits"]
knn_hits  = client.search(index=idx, knn={...})["hits"]["hits"]
fused = rrf_fuse([bm25_hits, knn_hits], k=60)[:k]  # see rrf_fuse() in notebook 06
```

## Bulk Indexing Pattern
```python
actions = [
    {"_index": INDEX_NAME, "_id": doc_id, "_source": source_dict}
    for doc_id, source_dict in documents
]
helpers.bulk(client, actions, chunk_size=200)
client.indices.refresh(index=INDEX_NAME)  # always refresh after bulk ingest
```

## Client-side RRF (never use ES native RRF — requires paid license)
```python
def rrf_fuse(hit_lists: list[list[dict]], k: int = 60) -> list[dict]:
    scores, sources = {}, {}
    for hits in hit_lists:
        for rank, hit in enumerate(hits, start=1):
            doc_id = hit["_id"]
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            sources[doc_id] = hit.get("_source", {})
    return [
        {"_id": d, "_score": s, "_source": sources[d]}
        for d, s in sorted(scores.items(), key=lambda x: -x[1])
    ]
```

## Standard Base Index Fields
```python
# Always include in base index mappings:
"modality_group":    {"type": "keyword"}   # functional/structural/diffusion/fieldmap/quantitative/perfusion/pet/spectroscopy
"study_description": {"type": "text"}      # from dataset_description.json BIDSVersion/Name/description
# Dense vector: always 768d for all-mpnet-base-v2 and allenai/specter2 models
# Use 1024d only for BAAI/bge-large-en-v1.5 — requires a separate index
```

## Index Name Conventions
| Index | Encoder | Notes |
|-------|---------|-------|
| `neuroimaging` | `all-mpnet-base-v2` 768d | Base index, Notebooks 01–04 |
| `neuroimaging-poc1` | `all-mpnet-base-v2` 768d | PoC-1: query expansion + multi-field BM25 |
| `neuroimaging-poc2` | SPLADE + `all-mpnet-base-v2` | PoC-2: learned sparse + dense hybrid |
| `neuroimaging-poc3-specter2` | `allenai/specter2` 768d | PoC-3: domain-aware encoder |
| `neuroimaging-poc3-bge` | `BAAI/bge-large-en-v1.5` 1024d | PoC-3: SOTA general retrieval |

## Context-Aware Column Presets (for display)
```python
COLS_FUNCTIONAL = ["dataset", "suffix", "subject", "task", "TaskName",
                   "RepetitionTime", "EchoTime", "MagneticFieldStrength", "Manufacturer"]
COLS_STRUCTURAL = ["dataset", "suffix", "subject", "MagneticFieldStrength",
                   "Manufacturer", "ManufacturersModelName", "InversionTime",
                   "FlipAngle", "MRAcquisitionType"]
COLS_DIFFUSION  = ["dataset", "suffix", "subject", "MagneticFieldStrength",
                   "Manufacturer", "PhaseEncodingDirection", "SliceThickness"]
COLS_SCANNER    = ["dataset", "suffix", "subject", "Manufacturer",
                   "ManufacturersModelName", "MagneticFieldStrength", "InstitutionName"]
COLS_DEFAULT    = ["dataset", "suffix", "subject", "MagneticFieldStrength",
                   "Manufacturer", "TaskName", "description_text"]
```

## Multi-field BM25 (cross_fields)
```python
# Boost TaskName and SeriesDescription over the generic description_text field
resp = client.search(index=idx, query={
    "multi_match": {
        "query": expanded_query,
        "fields": ["description_text", "TaskName^2", "SeriesDescription^1.5", "TaskDescription"],
        "type": "cross_fields",
        "operator": "or"
    }
}, size=k)
```

## Rules
- **Never** use ES native RRF (`rank: {rrf: {}}`) — it requires Platinum/Enterprise license
- Use `source_excludes=["metadata_embedding"]` when fetching docs for display (vectors are large)
- For full index scans use `from_`/`size` pagination in a while loop — do NOT use scroll for small collections
- Check index existence before creating: `client.indices.exists(index=name)`
- Default `k=10` (not 5) for all search result displays
- `num_candidates` should be `k * 20` for kNN (never lower than 100)
- Always call `client.indices.refresh()` after bulk ingest before any search
- Set `request_timeout=120` — encoding + indexing can be slow on CPU

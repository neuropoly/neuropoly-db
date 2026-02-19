# Chapter 2 — ElasticSearch Architecture & Vector Search

*Reading time: ~15 minutes*

---

## Core Concepts Refresher

If you've seen ES before, skim this section. If not, these are the five things
you need to know:

### Index

An **index** is the top-level container for your data — think of it as a
database table. In our project, we'll have one index called `neuroimaging` that
stores one document per scan file.

### Document

A **document** is a JSON object stored in an index. Each scan in our BIDS
dataset becomes one document:

```json
{
  "subject": "01",
  "suffix": "bold",
  "task": "balloonanalogrisktask",
  "RepetitionTime": 2.0,
  "Manufacturer": "Siemens",
  "MagneticFieldStrength": 3.0,
  "SeriesDescription": "BOLD EPI resting state",
  "metadata_embedding": [0.032, -0.118, ...]
}
```

### Mapping

A **mapping** defines the schema of your index: which fields exist, what type
each field is, and how it should be indexed. Unlike traditional databases,
you *can* let ES auto-detect types, but for our use case explicit mapping is
critical — especially for the `dense_vector` field.

### Shards & Replicas

An index is split into **shards** (horizontal partitions) distributed across
nodes. Each shard can have **replicas** for redundancy. For our single-node
local setup, this is handled automatically — one shard, zero replicas. At
production scale, shard count affects kNN search behavior (candidates are
gathered per-shard, then merged).

### Inverted Index vs. HNSW Graph

ES maintains two parallel data structures:

- **Inverted index** — Maps terms to documents. Powers keyword/BM25 full-text
  search. Example: the term "Siemens" points to documents 1, 4, 7, 12.
- **HNSW graph** — Maps vectors to their nearest neighbors. Powers approximate
  kNN search. Example: the vector for document 1 links to documents 4, 7 (its
  nearest neighbors in embedding space).

Both can be queried simultaneously in a hybrid search.

---

## Field Types That Matter

You don't need to know all 30+ ES field types. Here are the five we'll use:

### `keyword`

Exact-match string. No tokenization or analysis. Used for categorical values
where you want exact equality or aggregation:

```
"Manufacturer": "Siemens"    → term query: {"term": {"Manufacturer": "Siemens"}}
```

Use for: `subject`, `session`, `suffix`, `modality`, `task`, `Manufacturer`,
`ManufacturersModelName`, `sex`, `bids_path`.

### `text`

Full-text analyzed string. Tokenized, lowercased, stemmed by default. Supports
fuzzy matching and relevance scoring (BM25):

```
"SeriesDescription": "BOLD EPI resting state sagittal"
→ match query: {"match": {"SeriesDescription": "resting bold"}}  // matches!
```

Use for: `SeriesDescription`, `TaskName`, `description_text`.

### `float` / `long`

Numeric types supporting range queries and aggregations:

```
"RepetitionTime": 2.0
→ range query: {"range": {"RepetitionTime": {"gte": 1.5, "lte": 3.0}}}
```

Use for: `RepetitionTime`, `EchoTime`, `FlipAngle`, `MagneticFieldStrength`,
`age`.

### `dense_vector`

The star of this course. Stores a fixed-length array of floats for kNN search:

```json
{
  "type": "dense_vector",
  "dims": 384,
  "similarity": "cosine",
  "index_options": {
    "type": "int8_hnsw"
  }
}
```

Key parameters:
- **`dims`** — Vector dimensionality (must match your embedding model; 384 for
  all-MiniLM-L6-v2). Max 4096.
- **`similarity`** — Distance metric: `cosine` (default), `l2_norm`,
  `dot_product`, `max_inner_product`.
- **`index_options.type`** — Quantization strategy. Options include `hnsw`
  (full float), `int8_hnsw`, `int4_hnsw`, `bbq_hnsw`, and flat variants.

> **ES 9.x default:** For dims ≥ 384, ES defaults to `bbq_hnsw`. We'll
> explicitly use `int8_hnsw` in this course for a good accuracy/memory balance
> without needing rescoring.

> **Important (ES 9.2+):** Dense vector values are **excluded from `_source`**
> by default. To retrieve vectors in search results, use the `fields` option
> explicitly.

---

## kNN Search in ElasticSearch

ES supports two kNN methods. We'll use both.

### Approximate kNN (primary method)

Uses the HNSW graph for fast, scalable retrieval. Added in ES 8.0:

```json
POST neuroimaging/_search
{
  "knn": {
    "field": "metadata_embedding",
    "query_vector": [0.032, -0.118, ...],
    "k": 10,
    "num_candidates": 100
  }
}
```

- **`k`** — Number of results to return.
- **`num_candidates`** — How many candidates HNSW explores per shard before
  selecting the top k. Higher = better recall, slower. Rule of thumb: 5–10× k.
- Returns the global top k across all shards (uses `dfs_query_then_fetch`
  automatically).

### Exact kNN (brute-force, for comparison)

Uses `script_score` with vector functions. Scans every matching document:

```json
POST neuroimaging/_search
{
  "query": {
    "script_score": {
      "query": {"match_all": {}},
      "script": {
        "source": "cosineSimilarity(params.qv, 'metadata_embedding') + 1.0",
        "params": {"qv": [0.032, -0.118, ...]}
      }
    }
  }
}
```

The `+ 1.0` is required because ES scores must be non-negative, and cosine
similarity ranges from -1 to 1.

**Use exact kNN only for:** small datasets, benchmarking recall of approximate
kNN, or when you need guaranteed accuracy.

---

## Filtered kNN

One of ES's killer features: **pre-filter before vector search**. The HNSW
graph is explored only within documents matching the filter:

```json
POST neuroimaging/_search
{
  "knn": {
    "field": "metadata_embedding",
    "query_vector": [0.032, -0.118, ...],
    "k": 5,
    "num_candidates": 50,
    "filter": {
      "term": {"suffix": "bold"}
    }
  }
}
```

This finds the 5 most semantically similar scans **among BOLD acquisitions
only**. The filter is applied during graph traversal, not after — so you're
guaranteed to get k results (if enough documents match the filter).

---

## Hybrid Search

Combine keyword/BM25 scoring with vector similarity in a single request:

```json
POST neuroimaging/_search
{
  "query": {
    "match": {
      "SeriesDescription": {
        "query": "resting state BOLD",
        "boost": 0.7
      }
    }
  },
  "knn": {
    "field": "metadata_embedding",
    "query_vector": [0.032, -0.118, ...],
    "k": 10,
    "num_candidates": 100,
    "boost": 0.3
  },
  "size": 10
}
```

Scoring:
```
final_score = 0.7 × BM25_score + 0.3 × kNN_score
```

The `query` and `knn` results are combined as a **disjunction** — a document
can appear from either pathway (or both). The `boost` parameters control the
relative weight. Tuning these is the art of hybrid search.

---

## The ES Python Client

The `elasticsearch` Python package (9.x) wraps the REST API:

```python
from elasticsearch import Elasticsearch, helpers

# Connect
client = Elasticsearch("http://localhost:9200")

# Create index with explicit mapping
client.indices.create(index="neuroimaging", body={
    "mappings": {
        "properties": {
            "subject": {"type": "keyword"},
            "RepetitionTime": {"type": "float"},
            "metadata_embedding": {
                "type": "dense_vector",
                "dims": 384,
                "similarity": "cosine",
                "index_options": {"type": "int8_hnsw"}
            }
        }
    }
})

# Bulk index documents
actions = [
    {"_index": "neuroimaging", "_source": doc}
    for doc in documents
]
helpers.bulk(client, actions)

# kNN search
results = client.search(
    index="neuroimaging",
    knn={
        "field": "metadata_embedding",
        "query_vector": query_vec.tolist(),
        "k": 10,
        "num_candidates": 100
    }
)

# Hybrid search
results = client.search(
    index="neuroimaging",
    query={"match": {"SeriesDescription": "T1w sagittal"}},
    knn={
        "field": "metadata_embedding",
        "query_vector": query_vec.tolist(),
        "k": 5,
        "num_candidates": 50
    }
)
```

The `helpers.bulk()` function is essential for ingesting many documents
efficiently — it batches requests and handles errors/retries.

---

## Key Reference Links

- [Dense vector field type](https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/dense-vector) — Mapping options, quantization, index options
- [kNN search in ES](https://www.elastic.co/docs/solutions/search/vector/knn) — Approximate, exact, filtered, hybrid
- [Python client docs](https://elasticsearch-py.readthedocs.io/) — API reference
- [Approximate kNN tuning guide](https://www.elastic.co/docs/deploy-manage/production-guidance/optimize-performance/approximate-knn-search) — Performance optimization

---

**Next:** [Chapter 3 — BIDS Metadata for Search](03-bids-metadata.md)

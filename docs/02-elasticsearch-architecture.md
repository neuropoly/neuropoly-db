# Chapter 2 — ElasticSearch Architecture & Vector Search

*Reading time: ~20 minutes*

This chapter follows a narrative arc: you have neuroimaging metadata and you
want to search it. We'll build up from how ES stores and finds data, through
text search and vector search, to the hybrid combination that powers our
search engine.

---

## Part 1 — The Data Model

### Index, Document, Mapping

An **index** is the top-level container — analogous to a database table. Our
project has one index called `neuroimaging` that stores one document per scan.

A **document** is a JSON object:

```json
{
  "dataset": "ds000117",
  "subject": "01",
  "suffix": "bold",
  "task": "facerecognition",
  "RepetitionTime": 2.0,
  "Manufacturer": "SIEMENS",
  "MagneticFieldStrength": 3.0,
  "InstitutionName": "MRC Cognition and Brain Sciences Unit",
  "description_text": "BOLD functional MRI | task: facerecognition | 3.0T | SIEMENS ...",
  "metadata_embedding": [0.032, -0.118, ...]
}
```

A **mapping** defines the schema: field names, types, and how each should be
indexed. Unlike traditional databases, you *can* let ES auto-detect types, but
for our use case explicit mapping is critical — especially for the
`dense_vector` field.

### Shards & Replicas

An index is split into **shards** (horizontal partitions) distributed across
nodes. Each shard can have **replicas** for redundancy. For our single-node
local setup: one shard, zero replicas. At production scale, shard count affects
kNN behavior (candidates are gathered per-shard, then merged).

---

## Part 2 — Text Search: Inverted Index and BM25

How does ES find documents containing a word like "Siemens" among millions of
documents? The answer is the **inverted index** — a data structure that maps
terms to document IDs.

### The Inverted Index

When you index a document, ES tokenizes and normalizes each `text` field. The
resulting terms are stored in a map:

```
                      Inverted Index (field: description_text)
    ┌──────────────┬──────────────────────────────┐
    │ Term         │ Posting List (doc IDs)        │
    ├──────────────┼──────────────────────────────┤
    │ "bold"       │ [1, 3, 5, 8, 12, 15, ...]    │
    │ "functional" │ [1, 3, 5, 8, 12, 15, ...]    │
    │ "siemens"    │ [1, 3, 5, 22, 30, ...]        │
    │ "t1"         │ [2, 4, 6, 10, 14, ...]        │
    │ "weighted"   │ [2, 4, 6, 10, 14, ...]        │
    │ "diffusion"  │ [7, 9, 20, ...]               │
    │ "1.5t"       │ [22, 30, 31, ...]             │
    │ "3.0t"       │ [1, 2, 3, 5, 8, ...]          │
    └──────────────┴──────────────────────────────┘
```

To find documents matching "BOLD functional", ES:
1. Looks up "bold" → gets posting list `[1, 3, 5, 8, ...]`
2. Looks up "functional" → gets posting list `[1, 3, 5, 8, ...]`
3. Intersects (for AND) or unions (for OR) the lists
4. Scores each matching document using BM25

Each posting list is stored sorted and compressed, enabling $O(\log n)$
intersection via skip lists. This is why keyword search scales to billions of
documents.

### BM25 Scoring

The **match** query uses **BM25** (Best Match 25) to rank results by relevance.
For a query $Q$ containing terms $q_1, q_2, \ldots, q_n$ against a document
$D$:

$$\text{BM25}(D, Q) = \sum_{i=1}^{n} \text{IDF}(q_i) \cdot \frac{f(q_i, D) \cdot (k_1 + 1)}{f(q_i, D) + k_1 \cdot \left(1 - b + b \cdot \frac{|D|}{\text{avgdl}}\right)}$$

Where:
- $f(q_i, D)$ = frequency of term $q_i$ in document $D$
- $|D|$ = document length (in tokens)
- $\text{avgdl}$ = average document length across the index
- $k_1 = 1.2$ (term frequency saturation) — diminishing returns for repeated terms
- $b = 0.75$ (length normalization) — shorter documents get a boost
- $\text{IDF}(q_i) = \ln\left(\frac{N - n(q_i) + 0.5}{n(q_i) + 0.5} + 1\right)$ — rarer terms score higher

**Intuition:** BM25 rewards documents that contain the query terms (especially
rare ones), penalizes very long documents, and has diminishing returns for
repeated terms. It's a carefully tuned balance of term frequency and document
relevance that has remained the gold standard for 20+ years.

---

## Part 3 — Vector Search: HNSW in ES

### Field Types Overview

| ES type | Use case | Examples |
|---------|----------|---------|
| `keyword` | Exact match, aggregation | `Manufacturer`, `suffix`, `dataset` |
| `text` | Full-text BM25 search | `SeriesDescription`, `TaskName` |
| `float` | Range queries, stats | `RepetitionTime`, `EchoTime`, `age` |
| `dense_vector` | kNN similarity search | `metadata_embedding` (384 dims) |

### Dense Vector Configuration

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

- **`dims`** — Must match the embedding model (384 for all-MiniLM-L6-v2). Max 4096.
- **`similarity`** — Distance metric: `cosine` (default), `l2_norm`,
  `dot_product`, `max_inner_product`.
- **`index_options.type`** — Quantization: `hnsw` (full float), `int8_hnsw`,
  `int4_hnsw`, `bbq_hnsw`, and flat variants.

> **ES 9.x default:** For dims ≥ 384, ES defaults to `bbq_hnsw`. We explicitly
> use `int8_hnsw` for a good accuracy/memory balance without rescoring.

> **Important (ES 9.2+):** Dense vector values are **excluded from `_source`**
> by default. To retrieve vectors in search results, use the `fields` option.

### Approximate kNN Search

Uses the HNSW graph for fast, scalable retrieval:

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

- **`k`** — Number of results to return
- **`num_candidates`** — HNSW exploration width per shard. Higher = better recall, slower. Rule of thumb: 5–10× k.

### Exact kNN (Brute Force)

For benchmarking or tiny datasets, use `script_score`:

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

The `+ 1.0` shifts cosine similarity from $[-1, 1]$ to $[0, 2]$ because ES
scores must be non-negative.

### Filtered kNN

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
      "term": {"dataset": "ds000117"}
    }
  }
}
```

The filter is applied **during** graph traversal, not after — so you're
guaranteed to get $k$ results that match (if enough documents qualify).

---

## Part 4 — Hybrid Search

Pure vector search finds semantically similar results but might miss exact
keyword matches. Pure BM25 finds exact matches but misses semantic
relationships. **Hybrid search** combines both:

```
    ┌───────────────────────┐         ┌───────────────────────┐
    │   BM25 Text Search     │         │   kNN Vector Search    │
    │   (inverted index)     │         │   (HNSW graph)         │
    │                        │         │                        │
    │  "Siemens 3T resting"  │         │  query_vector (384d)   │
    └───────────┬────────────┘         └───────────┬────────────┘
                │                                  │
                │  BM25 score                      │  kNN score
                │  (range: 0 – ~15)                │  (range: 0 – ~1)
                │                                  │
                └──────────┬───────────────────────┘
                           │
                           ▼
                    Score combination:
              final = boost_bm25 × BM25_score
                    + boost_knn  × kNN_score

                    Documents from either or both
                    pathways are merged (disjunction)
```

### Score Normalization

A subtlety: BM25 scores and kNN scores live on **different scales**.

- **BM25** scores depend on term frequency, document length, and IDF. Typical
  range: 0 to ~15 (unbounded, varies by index).
- **kNN cosine** scores range from 0 to 1 (or +1 offset: 1 to 2).

ES combines them as a **weighted sum** using the `boost` parameter:

```json
{
  "query": {
    "match": {
      "description_text": {"query": "Siemens resting state", "boost": 0.3}
    }
  },
  "knn": {
    "field": "metadata_embedding",
    "query_vector": [...],
    "k": 10,
    "num_candidates": 100,
    "boost": 0.7
  }
}
```

The `boost` values are multiplicative weights applied to each raw score before
summation. Because BM25 scores are typically larger than kNN scores, you often
want a lower boost for BM25 to prevent it from dominating. Tuning
these weights is the art of hybrid search — there is no single correct ratio.

A document can appear from **either** pathway (or both). This is a disjunction:
a document matched only by BM25 still appears (with kNN score = 0), and vice
versa.

---

## Part 5 — The ES Python Client

The `elasticsearch` Python package (9.x) wraps the REST API. Here are the key
operations using **keyword arguments** (not the deprecated `body=` parameter):

```python
from elasticsearch import Elasticsearch, helpers

# Connect
client = Elasticsearch("http://localhost:9200")

# Create index with explicit mapping
client.indices.create(
    index="neuroimaging",
    settings={"number_of_replicas": 0},
    mappings={
        "properties": {
            "dataset":    {"type": "keyword"},
            "subject":    {"type": "keyword"},
            "RepetitionTime": {"type": "float"},
            "metadata_embedding": {
                "type": "dense_vector",
                "dims": 384,
                "similarity": "cosine",
                "index_options": {"type": "int8_hnsw"}
            }
        }
    }
)

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
    query={"match": {"description_text": "T1w sagittal"}},
    knn={
        "field": "metadata_embedding",
        "query_vector": query_vec.tolist(),
        "k": 5,
        "num_candidates": 50
    }
)
```

The `helpers.bulk()` function batches requests and handles errors/retries —
essential for efficient ingestion.

### Dual Data Structure Diagram

ES maintains two parallel data structures for each index:

```
    ┌──────────────────────────────────────────────────────────┐
    │                   ElasticSearch Index                      │
    │                                                           │
    │  ┌─────────────────────┐     ┌─────────────────────────┐ │
    │  │   Inverted Index     │     │   HNSW Graph             │ │
    │  │   (Lucene segment)   │     │   (dense_vector index)   │ │
    │  │                      │     │                          │ │
    │  │  term → [doc IDs]    │     │   node ─── neighbors     │ │
    │  │                      │     │    │ ╲                    │ │
    │  │  "siemens" → [1,3,5] │     │   node ─── neighbors     │ │
    │  │  "bold"    → [1,3,8] │     │    │ ╲ ╲                 │ │
    │  │  "3.0t"    → [1,2,5] │     │   node ─── neighbors     │ │
    │  │                      │     │                          │ │
    │  │  Powers: match, term │     │  Powers: knn, filtered   │ │
    │  │  range, bool, aggs   │     │  knn, hybrid (vector     │ │
    │  │  (BM25 scoring)      │     │  component)              │ │
    │  └─────────────────────┘     └─────────────────────────┘ │
    │                                                           │
    │          Both queried simultaneously in hybrid search      │
    └──────────────────────────────────────────────────────────┘
```

---

## Key Reference Links

- [Dense vector field type](https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/dense-vector) — Mapping options, quantization, index options
- [kNN search in ES](https://www.elastic.co/docs/solutions/search/vector/knn) — Approximate, exact, filtered, hybrid
- [Python client docs](https://elasticsearch-py.readthedocs.io/) — API reference
- [BM25 in Lucene](https://lucene.apache.org/core/9_0_0/core/org/apache/lucene/search/similarities/BM25Similarity.html) — Scoring implementation
- [Approximate kNN tuning guide](https://www.elastic.co/docs/deploy-manage/production-guidance/optimize-performance/approximate-knn-search) — Performance optimization

---

**Next:** [Chapter 3 — BIDS Metadata for Search](03-bids-metadata.md)

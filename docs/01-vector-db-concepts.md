# Chapter 1 — Vector Database Concepts

*Reading time: ~15 minutes*

---

## Why Vector Databases?

Traditional databases excel at **exact matching**: give me all rows where
`Manufacturer = "Siemens"` and `MagneticFieldStrength = 3.0`. But what if you
want to ask a fuzzier question?

> *"Find scans that are similar to a T1-weighted sagittal brain acquisition
> on a 3T Siemens Prisma with 1mm isotropic resolution."*

This is a **semantic similarity** problem. The query is natural language; the
data is structured metadata. A traditional `WHERE` clause can't capture the
nuance of "similar to." This is where vector databases come in.

---

## Embeddings: Turning Data into Vectors

An **embedding** is a dense numerical representation of data — an array of
floating-point numbers (typically 128–1024 dimensions) produced by a machine
learning model. The key property:

> **Semantically similar inputs map to nearby points in vector space.**

For example, a sentence-transformer model encodes text into a 384-dimensional
vector:

```
"T1w anatomical brain scan 3T Siemens"  →  [0.032, -0.118, 0.045, ..., 0.091]
"structural MRI 3 Tesla Siemens Prisma" →  [0.029, -0.121, 0.042, ..., 0.088]
"resting state fMRI 1.5T GE scanner"    →  [0.510, 0.203, -0.331, ..., -0.045]
```

The first two vectors are *close* to each other (high cosine similarity); the
third is *far* (low similarity). The model has learned that "T1w anatomical"
and "structural MRI" mean similar things, while "resting state fMRI" is a
different concept.

### How Embeddings Are Generated

We'll use [`sentence-transformers/all-MiniLM-L6-v2`](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2),
a popular lightweight model:

- **384 dimensions** per vector
- Trained on over 1 billion text pairs
- Fast inference on CPU (no GPU needed for our dataset size)
- Supports English text up to 256 tokens

For our use case, we **concatenate key metadata fields into a readable string**
and then encode it:

```python
text = "T1w | 3.0T | Siemens Prisma | RepetitionTime=2.3s | InstitutionName=CMU"
vector = model.encode(text)  # → numpy array of shape (384,)
```

The resulting vector captures the *meaning* of this scan's identity.

---

## Similarity Metrics

Once you have vectors, you need a way to measure how "close" two vectors are.
The three main metrics:

### Cosine Similarity

Measures the **angle** between two vectors, ignoring magnitude. A value of 1.0
means identical direction; 0.0 means orthogonal; -1.0 means opposite.

$$\text{cosine}(\mathbf{a}, \mathbf{b}) = \frac{\mathbf{a} \cdot \mathbf{b}}{||\mathbf{a}|| \cdot ||\mathbf{b}||}$$

**Best for NLP embeddings** — most sentence-transformer models are trained to
produce unit-normalized vectors, making cosine the natural metric. This is the
ES default.

### L2 (Euclidean) Distance

Measures the **straight-line distance** in vector space.

$$L_2(\mathbf{a}, \mathbf{b}) = \sqrt{\sum_{i} (a_i - b_i)^2}$$

Sensitive to vector magnitude. Useful when magnitude carries information (e.g.,
image pixel intensities).

### Dot Product

$$\text{dot}(\mathbf{a}, \mathbf{b}) = \sum_{i} a_i \cdot b_i$$

Equivalent to cosine similarity when vectors are unit-normalized. Slightly
faster to compute.

**For this course, we use cosine similarity throughout.**

---

## The Scalability Problem: Why Not Brute Force?

A brute-force k-nearest-neighbor (kNN) search compares the query vector against
*every* vector in the database. With $n$ documents and $d$ dimensions:

- Time complexity: $O(n \times d)$
- For 1 million documents at 384 dims: ~384 million floating-point operations
  per query

This works for small datasets (hundreds to low thousands) but becomes
prohibitively slow at scale. We need **Approximate Nearest Neighbor (ANN)**
algorithms.

---

## HNSW: The Algorithm Behind ES Vector Search

**Hierarchical Navigable Small World** (HNSW) is the dominant ANN algorithm used
by ElasticSearch (and most other vector databases). Published by Malkov &
Yashunin in 2016: https://arxiv.org/abs/1603.09320

### Intuition

Imagine a city with neighborhoods. To find a specific address:

1. **Top layer** — You start on the highway system (sparse, long-range
   connections). You navigate to the right general area quickly.
2. **Middle layers** — You exit onto local roads (denser connections). You
   narrow down to the right neighborhood.
3. **Bottom layer** — You walk along residential streets (very dense
   connections). You find the exact house.

HNSW builds a **multi-layer graph** with this property:

```
Layer 3 (sparse):    A ──────────────── D
                     │                  │
Layer 2:             A ──── B ───── C ── D
                     │      │      │    │
Layer 1:             A ─ B ─ E ─ C ─ F ─ D
                     │   │   │   │   │   │
Layer 0 (dense):     A B G E H C I F J D K
```

- Upper layers have **few nodes, long-range links** → fast coarse navigation
- Lower layers have **all nodes, short-range links** → precise refinement

### Key Parameters

| Parameter | What it controls | Default | Trade-off |
|-----------|-----------------|---------|-----------|
| `m` | Max connections per node in the graph | 16 | Higher = better recall, more memory, slower indexing |
| `ef_construction` | Search width during index building | 100 | Higher = better graph quality, slower indexing |
| `num_candidates` | Search width at query time | (per query) | Higher = better recall, slower queries |

The `num_candidates` parameter is the **primary knob** for tuning the
speed/accuracy trade-off at query time. A typical good starting ratio is
`num_candidates = 10 × k` (e.g., 100 candidates for k=10 results).

---

## Quantization: Trading Accuracy for Memory

Storing 384 floats × 4 bytes = **1,536 bytes per vector**. At 1 million
documents, that's ~1.5 GB just for vectors. Quantization compresses each vector:

| Strategy | Memory savings | How it works | Accuracy impact |
|----------|---------------|--------------|-----------------|
| **int8** | ~75% (4×) | Each float32 dimension → 1-byte integer | Minimal |
| **int4** | ~87% (8×) | Each float32 → half-byte integer | Moderate |
| **bbq** | ~96% (32×) | Each dimension → single bit | Significant (mitigated by rescoring) |

ElasticSearch 9.x automatically quantizes:
- **≥384 dims** → `bbq_hnsw` (aggressive compression, rescoring recommended)
- **<384 dims** → `int8_hnsw` (mild compression)

**Rescoring** compensates for quantization loss: after finding approximate
candidates with quantized vectors, ES re-ranks the top results using the
original full-precision vectors. This recovers most of the accuracy.

---

## Hybrid Search: The Best of Both Worlds

Pure vector search finds semantically similar results but might miss exact
matches. Pure keyword search finds exact matches but misses semantic
relationships. **Hybrid search** combines both:

```
┌──────────────┐        ┌───────────────┐
│ Keyword/BM25  │        │  kNN Vector    │
│ "3T Siemens"  │        │  query_vector  │
└──────┬───────┘        └──────┬────────┘
       │                       │
       ▼                       ▼
  match_score              knn_score
       │                       │
       └───────┬───────────────┘
               ▼
    final_score = w₁ × match_score + w₂ × knn_score
```

In ElasticSearch, the `query` and `knn` clauses are combined as a
**disjunction** (boolean OR) — a document can match either or both, and scores
are summed with configurable boost weights.

This is **particularly powerful for neuroimaging metadata**:
- Use keyword search to filter by exact technical parameters (3T, Siemens, T1w)
- Use vector search to capture semantic meaning ("high-resolution structural
  brain scan")
- Combine them to get results that match both technically and semantically

---

## Vector DBs vs. ElasticSearch: Why ES?

| Feature | Purpose-built Vector DB (Pinecone, Weaviate, Milvus) | ElasticSearch |
|---------|------------------------------------------------------|---------------|
| Pure vector search | Optimized from the ground up | Very capable (since v8.0, 2022) |
| Full-text keyword search | Limited or absent | **Best-in-class** (BM25, analyzers) |
| Structured filters & aggregations | Basic | **Extremely rich** (ranges, terms, histograms, nested) |
| Analytics & dashboards | None | **Kibana** — integrated visualization |
| Hybrid search | Varies | **Native** (query + knn in one request) |
| Operational maturity | Newer ecosystems | Battle-tested at scale for 15+ years |

For neuroimaging metadata, we need *all* of this: exact filters on scanner
parameters, full-text search on descriptions, semantic similarity, aggregation
dashboards. ES is the natural fit.

---

**Next:** [Chapter 2 — ElasticSearch Architecture & Vector Search](02-elasticsearch-architecture.md)

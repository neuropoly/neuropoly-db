# Chapter 1 — Vector Database Concepts

*Reading time: ~25 minutes*

---

## From Databases to Vector Search: A Brief History

Data retrieval has evolved in waves, each driven by a new kind of question:

| Era | Technology | Query paradigm | Limitation |
|-----|-----------|----------------|------------|
| 1970s | **Relational DB** (SQL) | Exact match, joins, aggregations | Cannot rank by relevance |
| 1999 | **Lucene / full-text search** | Tokenized text, TF-IDF, BM25 | No understanding of *meaning* |
| 2010s | **NoSQL / document stores** | Flexible schemas, horizontal scale | Still keyword-based retrieval |
| 2020s | **Vector databases** | Nearest-neighbor in embedding space | New algorithms and trade-offs |

The breakthrough that enabled vector search was **representation learning**:
deep neural networks that compress high-dimensional data (text, images, audio)
into dense vectors where geometric distance reflects semantic similarity. Once
you have those vectors, the retrieval problem becomes: *given a query vector,
find the $k$ closest stored vectors as fast as possible*.

ElasticSearch bridges era 2 and era 4 — it started as a full-text search engine
built on Lucene, and since version 8.0 (2022) also supports native vector
search via HNSW indexes. That dual capability is exactly why we use it for this
course.

---

## Why ES Over a Purpose-Built Vector DB?

Before diving into the theory, it helps to understand *why ElasticSearch* rather
than a dedicated vector database like Pinecone, Weaviate, or Milvus:

| Feature | Purpose-built Vector DB | ElasticSearch |
|---------|------------------------|---------------|
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

## The Semantic Similarity Problem

Traditional databases excel at **exact matching**: give me all rows where
`Manufacturer = "Siemens"` and `MagneticFieldStrength = 3.0`. But what if you
want to ask a fuzzier question?

> *"Find scans that are similar to a T1-weighted sagittal brain acquisition
> on a 3T Siemens Prisma with 1mm isotropic resolution."*

This is a **semantic similarity** problem. The query is natural language; the
data is structured metadata. A traditional `WHERE` clause can't capture the
nuance of "similar to." This is where vector search comes in.

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

### Worked Example: Cosine Similarity by Hand

To build intuition, here's a concrete computation with 3-dimensional vectors
(our embeddings have 384 dimensions, but the math is identical):

```python
import numpy as np

a = np.array([0.5, 0.3, 0.8])   # "T1w anatomical 3T Siemens"
b = np.array([0.4, 0.6, 0.7])   # "structural MRI 3 Tesla"
c = np.array([-0.2, 0.9, -0.1]) # "resting state fMRI 1.5T GE"

def cosine_sim(x, y):
    return np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y))

print(f"sim(a, b) = {cosine_sim(a, b):.4f}")  # ≈ 0.8981 — high (similar scans)
print(f"sim(a, c) = {cosine_sim(a, c):.4f}")  # ≈ 0.1690 — low (different modality)
```

Step by step for $\text{sim}(\mathbf{a}, \mathbf{b})$:

$$\mathbf{a} \cdot \mathbf{b} = (0.5)(0.4) + (0.3)(0.6) + (0.8)(0.7) = 0.20 + 0.18 + 0.56 = 0.94$$

$$||\mathbf{a}|| = \sqrt{0.25 + 0.09 + 0.64} = \sqrt{0.98} \approx 0.9899$$

$$||\mathbf{b}|| = \sqrt{0.16 + 0.36 + 0.49} = \sqrt{1.01} \approx 1.0050$$

$$\text{cosine}(\mathbf{a}, \mathbf{b}) = \frac{0.94}{0.9899 \times 1.0050} \approx 0.945$$

The closer to 1.0, the more similar. In 384 dimensions the principle is the
same — just more additions in the dot product and the norms.

---

## The Scalability Problem: From Brute Force to ANN

### Exact kNN: The Baseline

A brute-force k-nearest-neighbor (kNN) search compares the query vector against
*every* vector in the database. With $n$ documents and $d$ dimensions:

- Time complexity: $O(n \times d)$
- For 1 million documents at 384 dims: ~384 million floating-point operations
  per query

This works for small datasets (hundreds to low thousands) but becomes
prohibitively slow at scale.

### The Curse of Dimensionality

In low dimensions (2D, 3D), spatial index structures like KD-trees give
$O(\log n)$ exact nearest-neighbor lookup. But their performance degrades
rapidly as dimensions increase — in $d \geq 20$ dimensions, KD-trees
degenerate to brute-force scan. This is the curse of dimensionality:

> In high-dimensional spaces, all points tend to become roughly equidistant
> from each other, making distance-based partitioning ineffective.

For $d = 384$, we cannot use exact spatial indexes efficiently.

### Approximate Nearest Neighbor (ANN)

The solution is to relax the exactness guarantee. An **ANN algorithm** finds
vectors that are *probably* among the closest, with high probability:

> **Problem (c-ANN):** Given a dataset $S$ of $n$ points in $\mathbb{R}^d$, a
> query point $q$, and an approximation factor $c > 1$: return a point
> $p \in S$ such that $\|p - q\| \leq c \cdot \|p^* - q\|$, where $p^*$ is
> the true nearest neighbor.

Key ANN approaches and their complexities:

| Algorithm | Query time | Space | Notes |
|-----------|-----------|-------|-------|
| Brute force | $O(nd)$ | $O(nd)$ | Exact, impractical at scale |
| KD-tree | $O(2^d \log n)$ | $O(nd)$ | Exact but degrades badly for $d > 20$ |
| LSH | $O(n^{1/c})$ | $O(n^{1+1/c})$ | Sublinear; randomized; tunable accuracy |
| **HNSW** | **$O(d \log n)$** | $O(nd \cdot M)$ | **Dominant in practice**; graph-based |

HNSW achieves $O(\log n)$ graph hops, each costing $O(d)$ for a distance
computation, giving $O(d \log n)$ total — dramatically better than brute
force. This is the algorithm ElasticSearch uses.

---

## HNSW: The Algorithm Behind ES Vector Search

**Hierarchical Navigable Small World** (HNSW) is the dominant ANN algorithm used
by ElasticSearch (and most other vector databases). Published by Malkov &
Yashunin in 2016: https://arxiv.org/abs/1603.09320

### Small World Graphs

The foundation of HNSW is the **navigable small world** phenomenon (related to
the "six degrees of separation" concept): in a graph where nodes have a mix of
short-range and long-range connections, any node can reach any other in
$O(\log n)$ hops via **greedy routing** — always moving to the neighbor closest
to the target.

A single-layer navigable small world graph works well but requires careful
tuning. HNSW adds a **hierarchy** to make it robust.

### Intuition: The Highway System

Imagine navigating a city:

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

### How Search Works

Given a query vector $q$:

1. Start at the entry point in the **top layer**
2. **Greedy walk**: move to the neighbor closest to $q$ until no improvement
3. **Drop down** one layer, carrying the current best node as the starting point
4. Repeat steps 2–3 until reaching **layer 0**
5. In layer 0, perform a broader beam search (exploring `ef` candidates)
6. Return the top $k$ results

Each layer transition narrows the search region exponentially, giving the
$O(\log n)$ property.

### Construction Algorithm

When inserting a new vector $v$:

1. **Assign a random layer** $l$ drawn from an exponential distribution:
   $l = \lfloor -\ln(\text{uniform}(0,1)) \times m_L \rfloor$ where
   $m_L = 1/\ln(M)$. Most nodes land on layer 0; few reach upper layers.
2. **Search for neighbors** from the top layer down to $l$, then from $l$
   down to 0, finding the $M$ nearest nodes at each layer.
3. **Connect** $v$ to those neighbors (bidirectional edges), pruning if any
   node exceeds its max connections.

### Complexity Analysis

| Operation | Time | Space |
|-----------|------|-------|
| **Single query** | $O(d \cdot \log n \cdot \text{ef})$ | — |
| **Insert one vector** | $O(d \cdot M \cdot \log n)$ | — |
| **Total index** ($n$ vectors) | $O(n \cdot d \cdot M \cdot \log n)$ | $O(n \cdot d + n \cdot M \cdot L)$ |

Where $L$ is the average number of layers (typically $\sim \log n$).

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

### How int8 Quantization Works

Each float32 value is linearly mapped to the $[0, 255]$ integer range:

$$q_i = \text{round}\left(\frac{x_i - x_{\min}}{x_{\max} - x_{\min}} \times 255\right)$$

where $x_{\min}$ and $x_{\max}$ are the per-dimension min/max across the
index. At query time, distances are computed using the quantized values. The
error is bounded by the quantization step size
$\delta = (x_{\max} - x_{\min}) / 255$, which for typical normalized
embeddings is negligible.

### ElasticSearch Defaults

ElasticSearch 9.x automatically selects a quantization strategy:
- **≥384 dims** → `bbq_hnsw` (aggressive compression, rescoring recommended)
- **<384 dims** → `int8_hnsw` (mild compression)

**Rescoring** compensates for quantization loss: after finding approximate
candidates with quantized vectors, ES re-ranks the top results using the
original full-precision vectors. This recovers most of the accuracy.

In this course, we explicitly use `int8_hnsw` for a good accuracy/memory
balance without needing rescoring.

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

---

**Next:** [Chapter 2 — ElasticSearch Architecture & Vector Search](02-elasticsearch-architecture.md)

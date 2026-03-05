---
agent: vector-search
description: >
  Generate an Elasticsearch 9.3 DSL query for the neuropoly-db neuroimaging index.
  Specify the search mode (bm25, knn, or hybrid) and an optional filter.
---

Generate an Elasticsearch DSL query for the **neuropoly-db neuroimaging index**.

**Search query:** ${input:query}
**Mode:** ${input:mode|bm25|knn|hybrid} (choose one)
**Optional filter** (e.g. `suffix=bold`, `Manufacturer=Siemens`, leave blank for none): ${input:filter}

---

## Context
- **Index**: `neuroimaging-poc1`
- **BM25 field**: `description_text` (type: text) — assembled BIDS metadata
- **Vector field**: `metadata_embedding` (dense_vector, 768d, cosine similarity)
- **Filter fields** (keyword): `dataset`, `suffix`, `Manufacturer`, `task`, `datatype`
- **Float fields** for range filters: `MagneticFieldStrength`, `RepetitionTime`, `EchoTime`
- **Client**: `elasticsearch-py` v9 — use `client.search()` (not the legacy `body=` kwarg)
- **Encoder**: `SentenceTransformer("all-mpnet-base-v2", device="cpu")` — 768d

## Required Output
1. Python `client.search()` call as a code block
2. Brief explanation of the query strategy
3. For **hybrid**: show both the BM25 and kNN calls + the `rrf_fuse()` invocation
4. If a filter was specified, include it via `filter` clause inside `bool`

## Rules
- For hybrid: use **client-side RRF** (`rrf_fuse([], k=60)`) — never ES native RRF
- For kNN: set `num_candidates = k * 20`
- For BM25 + kNN in hybrid: retrieve `k * 3` from each, then fuse and slice to `k`
- Apply query expansion via `SYNONYM_MAP` if the query contains known neuroimaging terms

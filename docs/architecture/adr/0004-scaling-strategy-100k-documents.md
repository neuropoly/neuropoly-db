# ADR-0004: Scaling Strategy for 100,000+ Documents

**Date:** 2026-03-09  
**Status:** Accepted  
**Deciders:** Solo developer + AI assistant  
**Technical Story:** Scale from 4,400 POC scans to 100,000+ production scans with <200ms search latency

---

## Context

### Current State (POC)
- **Documents**: 4,400 neuroimaging scans from bids-examples
- **Index**: Single monolithic `neuroimaging` index
- **Hardware**: Development laptop (8GB RAM, 4 CPU cores)
- **Search Latency**: ~50-150ms (hybrid search with RRF fusion)
- **Ingestion**: Manual, one dataset at a time via scripts

### Production Requirements

**Scale Targets:**
- **Documents**: 100,000+ neuroimaging scans initially, growing to 500k+
- **Datasets**: 50-200 BIDS datasets (500-2000 scans each)
- **Users**: 5-10 initially (single lab) → 50-100 (multi-site)
- **Ingestion Rate**: 10,000 scans/day (background jobs)
- **Search Latency**: <200ms p95 for hybrid search
- **Availability**: 99% uptime (single site, maintenance windows OK)

**Growth Path:**
1. **Month 0-3**: Development (4,400 scans, 1 dev)
2. **Month 3-6**: Single lab deployment (10,000-20,000 scans, 5-10 users)
3. **Month 6-12**: Multi-site pilot (50,000-100,000 scans, 20-50 users)
4. **Year 2+**: Production (500,000+ scans, 100+ users)

### Key Challenges

1. **Index Size**:
   - 100k docs × ~2KB metadata × 1.5 overhead = **~300MB** index size
   - Add 100k × 768 dims × 4 bytes × 1.2 (quantized int8_hnsw) = **~369MB** for vectors
   - **Total: ~700MB** per 100k scans (manageable for single shard)

2. **Search Performance**:
   - BM25: O(n) scan of inverted index (fast for 100k docs)
   - kNN: HNSW graph traversal (O(log n), fast even at 1M docs)
   - **Bottleneck**: Python-level RRF fusion of two result sets

3. **Ingestion Throughput**:
   - PyBIDS parsing: ~10-50ms per NIfTI + JSON sidecar
   - Embedding generation: ~50-100ms per scan (CPU, batch helps)
   - Elasticsearch bulk indexing: ~10-50ms per batch (200 docs)
   - **Bottleneck**: Sequential encoding (not parallelized)

4. **Resource Constraints**:
   - Solo developer start: Single server, limited hardware
   - Must scale gracefully (no cliff where everything breaks)

---

## Decision

We will use a **dataset-based index strategy** with **index aliases** for unified search, optimized for solo developer deployment and gradual scaling.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   Search Alias: "neuroimaging"              │
│             (Points to all dataset-specific indices)         │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
┌───────▼────────┐  ┌────────▼─────────┐  ┌───────▼────────┐
│ neuroimaging-  │  │ neuroimaging-    │  │ neuroimaging-  │
│   ds000001     │  │   ds000002       │  │   ds000117     │
│  (1,205 docs)  │  │   (654 docs)     │  │  (2,891 docs)  │
└────────────────┘  └──────────────────┘  └────────────────┘
  1 shard, 0 replicas  1 shard, 0 replicas  1 shard, 0 replicas
         │                     │                     │
         └─────────────────────┴─────────────────────┘
                              │
                    ┌─────────▼──────────┐
                    │  Search API        │
                    │  - Query alias     │
                    │  - RRF fusion      │
                    │  - Result ranking  │
                    └────────────────────┘
```

**Key Design Choices:**

1. **Index-per-Dataset**:
   - Each BIDS dataset → separate Elasticsearch index
   - Naming: `neuroimaging-<dataset_id>` (e.g., `neuroimaging-ds000001`)
   - Unified access via alias: `neuroimaging` (points to all dataset indices)

2. **Single Shard per Index**:
   - Each dataset index: 1 primary shard, 0 replicas (dev/single-lab)
   - No over-sharding (common mistake: 5 shards for 1000 docs)
   - Scale: Add replica shards when multi-site deployment

3. **Alias-Based Search**:
   - Search endpoint queries alias `neuroimaging`, not individual indices
   - Elasticsearch distributes search across all backing indices
   - API logic unchanged (transparent to users)

4. **Batch Encoding**:
   - Encode descriptions in batches of 32-64 (5-10× faster)
   - Use sentence-transformers `encode()` batching
   - Parallelize across CPU cores with `torch.set_num_threads()`

5. **Resource Allocation**:
   - ES heap: 4GB (50% of 8GB system RAM)
   - ES disk: 20GB (30GB with overhead)
   - Celery workers: 2-4 workers (one per CPU core)

---

## Rationale

### Why Index-per-Dataset?

**Pros:**
1. **Operational Flexibility**:
   - Delete dataset: Drop one index (no re-indexing)
   - Re-index dataset: Replace one index (no downtime for others)
   - Dataset-level access control (future: per-dataset API keys)
2. **Resource Efficiency**:
   - One shard per dataset (500-2000 docs) is optimal
   - Avoid over-sharding overhead (coordinate nodes, merge threads)
3. **Debugging**:
   - Isolate issues to specific dataset
   - View dataset stats independently (Kibana, `_cat/indices`)
4. **Distributed Ready**:
   - Easy to shard large datasets later (split `neuroimaging-ukbiobank` to 10 shards)
   - Can move hot datasets to faster disks

**Cons:**
1. **More Indices**: 50-200 indices vs 1 (but Elasticsearch handles 1000+ indices fine)
2. **Alias Overhead**: Minor overhead for alias resolution (<1ms)

### Why Not Monolithic Index?

**Cons of Single Index:**
- ❌ Can't delete/re-index one dataset without re-indexing all
- ❌ Hard to scale (sharding by dataset ID requires routing)
- ❌ No dataset-level isolation
- ❌ Index settings apply to all datasets (can't tune per-dataset)

### Why Not Index-per-Subject?

**Too Granular:**
- ❌ 10,000+ indices for 100k scans (10 scans/subject)
- ❌ Overhead per index (~1-2MB metadata)
- ❌ Search across 10k indices is slow

### Alternatives Considered

**Elasticsearch Cross-Cluster Search:**
- ✅ Scale to multiple clusters
- ❌ Overkill for single-lab start
- ❌ Operational complexity (network, security)
- **Verdict**: Future option for multi-site (Year 2+)

**External Vector Database (Pinecone, Weaviate):**
- ✅ Specialized for vector search
- ❌ Another service to manage
- ❌ Elasticsearch already handles kNN well (HNSW)
- ❌ Need BM25 anyway (Elasticsearch)
- **Verdict**: Not justified

**PostgreSQL + pgvector:**
- ✅ Single database for metadata + vectors
- ❌ BM25 support is limited (full-text search less mature)
- ❌ HNSW indexing slower than Elasticsearch
- ❌ No native aggregations / analytics
- **Verdict**: Not suitable for search-heavy use case

---

## Performance Analysis

### Index Size Projections

| Scans | Metadata | Vectors (int8_hnsw) | Total       | Shards                     |
| ----- | -------- | ------------------- | ----------- | -------------------------- |
| 10k   | 30 MB    | 92 MB               | **~120 MB** | 10-20 (1 per dataset)      |
| 100k  | 300 MB   | 369 MB              | **~700 MB** | 50-100 (1 per dataset)     |
| 500k  | 1.5 GB   | 1.8 GB              | **~3.5 GB** | 100-200 (1 per dataset)    |
| 1M    | 3 GB     | 3.6 GB              | **~7 GB**   | 200-500 (some multi-shard) |

**Assumptions:**
- Metadata: ~2KB per scan (JSON sidecar fields)
- Vectors: 768 dims × 4 bytes × 1.2 overhead (int8_hnsw quantization saves 70%)
- Overhead: 1.5× for indexing structures (inverted index, doc values)

### Search Latency Targets

| Operation          | Current (4.4k) | Target (100k) | Notes                                    |
| ------------------ | -------------- | ------------- | ---------------------------------------- |
| BM25               | ~20ms          | ~50ms         | Scales O(n) but inverted index is cached |
| kNN (HNSW)         | ~30ms          | ~60ms         | HNSW is O(log n), minimal increase       |
| RRF Fusion         | ~10ms          | ~20ms         | Python-level merging, 2 result sets      |
| **Total (Hybrid)** | **~60ms**      | **~130ms**    | ✅ Under 200ms target                     |

**Assumptions:**
- ES on SSD (NVMe preferred)
- 4GB heap, default thread pools
- Warm cache (production workload)

### Ingestion Throughput

**Sequential Encoding (Current):**
```
1 scan = 10ms parse + 50ms encode + 1ms ES = 61ms
10,000 scans = 61ms × 10k = 610 seconds = ~10 minutes
```

**Batch Encoding (Optimized):**
```
Batch 64 scans:
- 64 scans parse = 64 × 10ms = 640ms
- Batch encode = 1 × 200ms (20× speedup) = 200ms
- Bulk index = 64 / 200 per batch = 1 batch × 50ms = 50ms
Total per batch: 890ms
10,000 scans = (10k / 64) × 890ms = 156 batches × 890ms = ~2.3 minutes
```

**Speedup: 10 min → 2.3 min (4.3× faster)**

**With 4 Parallel Workers:**
- 2.3 min / 4 workers = **~35 seconds for 10k scans** 🚀
- 100k scans = ~6 minutes (acceptable for background job)

---

## Implementation Details

### 1. Index Template

Create an index template `neuroimaging-template` to apply settings to all dataset indices:

```json
{
  "index_patterns": ["neuroimaging-*"],
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0,
      "refresh_interval": "30s",
      "codec": "best_compression"
    },
    "mappings": {
      "properties": {
        "dataset": { "type": "keyword" },
        "subject": { "type": "keyword" },
        "suffix": { "type": "keyword" },
        "description_text": { 
          "type": "text",
          "analyzer": "standard"
        },
        "metadata_embedding": {
          "type": "dense_vector",
          "dims": 768,
          "index": true,
          "similarity": "cosine",
          "index_options": {
            "type": "int8_hnsw",
            "m": 16,
            "ef_construction": 100
          }
        },
        "MagneticFieldStrength": { "type": "float" },
        "RepetitionTime": { "type": "float" },
        "EchoTime": { "type": "float" },
        "task": { "type": "keyword" }
      }
    }
  }
}
```

### 2. Alias Management

Create alias `neuroimaging` pointing to all dataset indices:

```python
# After ingesting dataset ds000001
client.indices.create(index="neuroimaging-ds000001")
client.indices.put_alias(
    index="neuroimaging-ds000001", 
    name="neuroimaging"
)

# Search via alias (queries all backing indices)
response = client.search(
    index="neuroimaging",  # Alias
    body=hybrid_query
)
```

### 3. Batch Encoding

```python
# scripts/ingest.py optimization
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("all-mpnet-base-v2", device="cpu")

def ingest_dataset_batched(layout, index_name, batch_size=64):
    """Ingest BIDS dataset with batched encoding."""
    scans = list(layout.get(return_type='filename', extension=['.nii.gz']))
    
    # Collect all descriptions first
    descriptions = []
    docs = []
    for scan_path in tqdm(scans, desc="Parsing BIDS"):
        metadata = extract_metadata(scan_path, layout)
        description = build_description_text(metadata)
        descriptions.append(description)
        docs.append(metadata)
    
    # Batch encode (much faster)
    embeddings = model.encode(
        descriptions,
        batch_size=batch_size,
        show_progress_bar=True,
        normalize_embeddings=True
    )
    
    # Prepare bulk actions
    actions = []
    for doc, embedding in zip(docs, embeddings):
        doc['metadata_embedding'] = embedding.tolist()
        actions.append({
            "_index": index_name,
            "_source": doc
        })
    
    # Bulk index
    from elasticsearch.helpers import bulk
    bulk(client, actions, chunk_size=200)
    client.indices.refresh(index=index_name)
```

### 4. Hardware Sizing

**Development (Month 0-3):**
- **RAM**: 8GB (4GB ES heap, 2GB OS, 2GB Python)
- **CPU**: 4 cores
- **Disk**: 20GB SSD
- **Scale**: 10,000-20,000 scans

**Single-Lab Production (Month 3-6):**
- **RAM**: 16GB (8GB ES heap, 4GB OS, 4GB Celery workers)
- **CPU**: 8 cores
- **Disk**: 50GB SSD
- **Scale**: 50,000-100,000 scans

**Multi-Site Production (Month 6-12):**
- **RAM**: 32GB (16GB ES heap, 8GB OS, 8GB workers)
- **CPU**: 16 cores
- **Disk**: 200GB NVMe SSD
- **Replicas**: 1 replica shard (2× index size, better query throughput)
- **Scale**: 500,000+ scans

**Cloud Equivalents:**
- Dev: AWS t3.large (2 vCPU, 8GB, $60/mo)
- Lab: AWS t3.xlarge (4 vCPU, 16GB, $120/mo) or m5.xlarge (4 vCPU, 16GB, $140/mo)
- Prod: AWS m5.2xlarge (8 vCPU, 32GB, $280/mo)

---

## Migration Path

### Phase 1: Development (Current)
- Single monolithic index `neuroimaging` (4,400 scans)
- No aliases (not needed yet)

### Phase 2: Refactor (Week 4-5)
- Migrate to dataset-based indices
- Script: `split_index_by_dataset.py`
- Create alias `neuroimaging` → all dataset indices
- **No downtime**: Alias swap after migration complete

### Phase 3: Optimize (Week 6-8)
- Implement batch encoding
- Add Celery workers for parallel ingestion
- Tune ES heap, thread pools

### Phase 4: Scale (Month 6+)
- Add replica shards (1 replica)
- Increase hardware (16GB → 32GB RAM)
- Multi-node cluster if needed (3 nodes: 1 master, 2 data)

---

## Consequences

### Positive

1. **Graceful Scaling**: Works from 10k to 1M+ scans without architecture change
2. **Operational Simplicity**: Delete/re-index one dataset at a time
3. **Cost Efficiency**: Start small (8GB laptop), scale as needed
4. **Performance Headroom**: <130ms hybrid search latency at 100k scans (under 200ms target)
5. **Solo-Friendly**: Automated ingestion (Celery), minimal ops burden

### Negative

1. **Index Proliferation**: 50-200 indices to manage (mitigated by ILM policies)
2. **Alias Complexity**: Must remember to add new indices to alias (automation needed)
3. **No Magic Scaling**: Still need to monitor and tune (heap, shards, replicas)

### Neutral

1. **Future Work**: Multi-node cluster, cross-cluster search (Year 2+)
2. **Cost**: ~$120-280/mo cloud hosting (or free if on-prem server available)

---

## Validation

We will validate this decision by:
- [ ] Ingest 10k scans in <5 minutes (batch encoding + parallel workers)
- [ ] Hybrid search latency <200ms at 100k scans (p95)
- [ ] Delete one dataset in <10 seconds (drop index)
- [ ] Re-index one dataset without affecting others
- [ ] Monitor ES heap, CPU, disk usage (Kibana Stack Monitoring)

**Success Criteria:**
- ✅ System handles 100k scans on 16GB RAM, 8 CPU cores
- ✅ Search latency <200ms (95th percentile)
- ✅ Ingestion throughput >1000 scans/minute
- ✅ 99% uptime (single-site, maintenance windows OK)

---

## References

- [Elasticsearch Sizing Guide](https://www.elastic.co/guide/en/elasticsearch/reference/current/size-your-shards.html)
- [ES Index vs. Alias](https://www.elastic.co/guide/en/elasticsearch/reference/current/aliases.html)
- [HNSW Vector Search Performance](https://www.elastic.co/guide/en/elasticsearch/reference/current/knn-search.html#tune-approximate-knn-for-speed-accuracy)
- [sentence-transformers Batching](https://www.sbert.net/docs/usage/semantic_textual_similarity.html#batching)

---

**Supersedes:** N/A  
**Superseded by:** N/A (may be superseded by multi-cluster approach in Year 2+)  
**Related:**
- ADR-0001 (FastAPI for API Layer) — API queries alias, not individual indices
- ADR-0002 (Celery for Async Jobs) — Parallel workers for batch ingestion
- SCALING.md (Architecture Document) — Detailed scaling implementation guide

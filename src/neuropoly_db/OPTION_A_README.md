# Option A: Elasticsearch Core Implementation

This directory contains the core Elasticsearch integration for NeuroPoly DB.

## ✅ What's Implemented

### Core Modules

1. **`core/config.py`** — Configuration management
   - Loads settings from environment variables
   - Sensible defaults for development
   - Production-ready with security settings

2. **`core/elasticsearch.py`** — Elasticsearch client & index template
   - Client factory (sync and async)
   - Index template for dataset-based indices
   - Alias management (`neuroimaging` → all dataset indices)
   - Health check utilities

3. **`core/embeddings.py`** — Sentence transformer wrapper
   - Singleton encoder (loaded once)
   - Batch encoding optimization
   - Query encoding convenience functions

## 🚀 Quick Start

### 1. Install Dependencies

Make sure you have the required packages:
```bash
source .venv/bin/activate
pip install pydantic-settings  # If not already installed
```

### 2. Setup Index Template

Run the setup script to create the Elasticsearch index template:
```bash
python scripts/setup_index_template.py
```

This creates the `neuroimaging-template` that will automatically apply to all indices matching `neuroimaging-*`.

### 3. Test the Implementation

```python
from neuropoly_db.core.elasticsearch import get_elasticsearch_client, check_elasticsearch_health
from neuropoly_db.core.embeddings import encode_query

# Connect to Elasticsearch
client = get_elasticsearch_client()

# Check health
health = check_elasticsearch_health(client)
print(f"Cluster status: {health['status']}")
print(f"Total docs: {health['total_docs']}")

# Encode a query
query_vector = encode_query("T1w brain scan at 3 Tesla")
print(f"Query embedding shape: {query_vector.shape}")  # Should be (768,)
```

## 📋 Index Template Details

The template is configured for optimal performance with 100k+ documents:

- **Pattern**: `neuroimaging-*`
- **Shards**: 1 primary per dataset (<10k docs optimal)
- **Replicas**: 0 (development), 1 (production)
- **Vector field**: 768d dense vector with int8_hnsw quantization
- **Text field**: `description_text` for BM25 search
- **Keyword fields**: dataset, subject, suffix, manufacturer, etc.

### Dataset-Based Index Strategy

Each BIDS dataset gets its own index:
```
neuroimaging-ds000001  (1,205 scans)
neuroimaging-ds000002    (654 scans)
neuroimaging-ds000117  (2,891 scans)
```

All unified under the `neuroimaging` alias for seamless search:
```python
# Search across ALL datasets
result = client.search(index="neuroimaging", body={...})
```

## 🧪 Testing Components

### Test Configuration
```python
from neuropoly_db.core.config import settings

print(f"ES Host: {settings.es_host}")
print(f"Embedding model: {settings.embedding_model}")
print(f"Default alias: {settings.default_index_alias}")
```

### Test Elasticsearch Client
```python
from neuropoly_db.core.elasticsearch import get_elasticsearch_client

client = get_elasticsearch_client()
assert client.ping(), "Elasticsearch not reachable"

info = client.info()
print(f"ES Version: {info['version']['number']}")
```

### Test Index Template
```python
from neuropoly_db.core.elasticsearch import get_elasticsearch_client

client = get_elasticsearch_client()

# Check template exists
templates = client.indices.get_index_template(name="neuroimaging-template")
print(f"Template: {templates['index_templates'][0]['name']}")

# View template details
import json
print(json.dumps(templates, indent=2))
```

### Test Embeddings
```python
from neuropoly_db.core.embeddings import encode_query, encode_batch

# Single query
query_emb = encode_query("T1w brain scan")
print(f"Query embedding: {query_emb.shape}")  # (768,)

# Batch encoding
texts = ["T1w scan", "T2w scan", "fMRI bold"]
embeddings = encode_batch(texts, batch_size=32)
print(f"Batch embeddings: {embeddings.shape}")  # (3, 768)
```

## 🔧 Configuration

### Environment Variables

Create a `.env` file in the project root:
```bash
# Elasticsearch
ES_HOST=http://localhost:9200
ES_TIMEOUT=120

# For production (security enabled)
ES_API_KEY=your-api-key-here
# OR
ES_USERNAME=elastic
ES_PASSWORD=your-password

# Embedding model
EMBEDDING_MODEL=all-mpnet-base-v2
EMBEDDING_DEVICE=cpu

# Index settings
DEFAULT_INDEX_ALIAS=neuroimaging
INDEX_PREFIX=neuroimaging-
```

## 📚 References

- **ADR-0004**: Scaling Strategy for 100k Documents
- **ROADMAP.md**: Phase 1, Weeks 1-2 (Infrastructure)
- [Elasticsearch Index Templates](https://www.elastic.co/guide/en/elasticsearch/reference/current/index-templates.html)
- [Dense Vector Type](https://www.elastic.co/guide/en/elasticsearch/reference/current/dense-vector.html)

## ✨ Next Steps

Once you've tested the core implementation:

1. **Refactor ingestion** — Move `scripts/ingest.py` logic to use these modules
2. **Build search functions** — Extract notebook search code to `core/search.py`
3. **Create API layer** — FastAPI endpoints using these core modules (Phase 1, Weeks 3-4)

---

**Status**: ✅ Option A Complete — Ready for integration testing

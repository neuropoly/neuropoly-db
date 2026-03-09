# NeuroPoly DB — Production Roadmap

**Version:** 1.0  
**Last Updated:** March 9, 2026  
**Status:** Planning Phase  
**Target Scale:** 100,000+ neuroimaging scans

---

## Executive Summary

This roadmap outlines the transformation of NeuroPoly DB from an educational proof-of-concept into a production-ready neuroimaging metadata search engine. The system will support:

- **Initial deployment**: Single-server, research lab environment
- **Future scale**: Multi-site, cloud-ready, 100k+ documents
- **User base**: Research circles initially, expanding to clinicians and surgeons
- **Development**: Solo developer + AI assistance

### Key Design Principles

1. **Start Simple, Architect for Scale** — Single-server MVP with multi-site design
2. **Python-First** — Leverage existing codebase and neuroimaging ecosystem
3. **Incremental Value** — Each milestone delivers immediate utility
4. **Future-Proof Interfaces** — APIs designed for auth/multi-tenancy/PACS expansion
5. **Metadata-Only** — No scan data storage (reduces infrastructure complexity)

---

## Current State (March 2026)

### ✅ Completed (Educational Course)
- Elasticsearch 9.3 + Kibana 9.3 Docker stack
- Python BIDS metadata extraction pipeline (`scripts/ingest.py`)
- Vector search with `all-mpnet-base-v2` (768d dense embeddings)
- Hybrid search (BM25 + kNN) with Python-side RRF fusion
- Educational notebooks (01-09) covering:
  - Setup and ingestion
  - Keyword search
  - Vector and hybrid search
  - Kibana exploration
  - Advanced query encoding (LLM expansion, SPLADE investigation)
- ~4,400 scans indexed from bids-examples datasets

### 🚧 Gaps to Address
1. No production deployment configuration
2. No API service layer
3. No CLI tooling for operations
4. No end-user interface (Kibana is too technical)
5. No authentication/authorization
6. No automated ingestion pipeline
7. No monitoring/observability
8. Index structure not optimized for 100k+ scale

---

## Phase 1: Production-Ready Backend (4 weeks)

**Goal:** Deployable search engine with security, API, and 100k+ document capacity

### Week 1-2: Infrastructure (Milestone 1.1)

**Deliverables:**
- `docker-compose.prod.yml` with security enabled
- Production Elasticsearch configuration:
  - Security (xpack, API keys, TLS)
  - Resource limits (4-6GB heap for 100k docs)
  - Volume persistence strategy
  - Health checks
- Index template for dataset-based indices:
  - Pattern: `neuroimaging-{dataset-name}`
  - Alias: `neuroimaging` → all indices
  - ILM policies for lifecycle management
- `scripts/deploy.sh` — one-command deployment
- `scripts/backup.sh` — automated ES snapshots
- Documentation: `docs/deployment/quickstart.md`

**Key Configuration Changes:**
```yaml
# elasticsearch.yml additions
xpack.security.enabled: true
xpack.security.authc.api_key.enabled: true

# Index strategy
PUT _index_template/neuroimaging-template
{
  "index_patterns": ["neuroimaging-*"],
  "template": {
    "mappings": { ... },
    "settings": {
      "number_of_shards": 2,
      "number_of_replicas": 0
    }
  }
}
```

### Week 3-4: FastAPI Backend (Milestone 1.2)

**Deliverables:**
- FastAPI application in `src/neuropoly_db/api/`
- Core endpoints:
  - `POST /api/v1/search` — Unified search (auto-detect mode)
  - `POST /api/v1/search/semantic` — Vector kNN
  - `POST /api/v1/search/keyword` — BM25
  - `POST /api/v1/search/hybrid` — BM25 + kNN + RRF
  - `GET /api/v1/datasets` — List datasets
  - `GET /api/v1/datasets/{name}` — Dataset details
  - `GET /api/v1/health` — Health check
  - `GET /api/v1/stats` — Index statistics
- Authentication framework (API key initially, extensible to OAuth2/SSO)
- OpenAPI documentation at `/docs`
- Docker container for API service
- Basic tests: `tests/api/test_search.py`

**Package Structure:**
```
src/neuropoly_db/
├── __init__.py
├── api/
│   ├── main.py              # FastAPI app
│   ├── dependencies.py      # ES client, auth
│   ├── models.py            # Pydantic schemas
│   └── routers/
│       ├── search.py
│       ├── datasets.py
│       └── health.py
├── core/
│   ├── config.py            # Settings from env
│   ├── elasticsearch.py     # ES client wrapper
│   └── embeddings.py        # Sentence transformers
└── ingestion/
    └── pipeline.py          # Refactored ingest.py
```

**Tech Stack:**
- FastAPI 0.110+
- elasticsearch-py 9.3
- sentence-transformers 2.5+
- pydantic-settings 2.2+

---

## Phase 2: CLI Tool (2 weeks)

**Goal:** Command-line interface for all operations

### Week 5-6: Typer CLI (Milestone 2.0)

**Deliverables:**
- CLI in `src/neuropoly_db/cli/`
- Commands:
  ```bash
  neuropoly-db deploy start|stop|restart|status
  neuropoly-db data ingest <path> [--async] [--watch]
  neuropoly-db data validate <path>
  neuropoly-db data list
  neuropoly-db data delete <dataset>
  neuropoly-db search semantic|keyword|hybrid "query"
  neuropoly-db config init|set|show
  neuropoly-db index create|reindex|alias|delete
  ```
- Configuration management (`~/.neuropoly-db/config.yaml`)
- Rich terminal formatting and progress bars
- Installable via `pip install -e .`
- Integration tests

**Tech Stack:**
- Typer (CLI framework)
- Rich (terminal formatting)
- httpx (API client)

---

## Phase 3: Automated Ingestion (3 weeks)

**Goal:** Watch directories, auto-ingest new datasets  
**Priority:** HIGH (user requirement)

### Week 7-9: Ingestion Pipeline (Milestone 3.0)

**Deliverables:**
- Directory watcher (watchdog) monitoring `/data/incoming`
- BIDS validation wrapper (`bids-validator`)
- Celery workers for async ingestion:
  - Task queue with Redis broker
  - Progress tracking
  - Error handling and retry logic
- API endpoints for ingestion:
  - `POST /api/v1/ingest` — Submit ingestion job
  - `GET /api/v1/ingest/{job_id}/status` — Check progress
  - `WS /api/v1/ingest/stream` — Real-time updates
- Docker Compose updates:
  - Redis service
  - Celery worker container
  - Ingestion watcher container

**Architecture:**
```
/data/incoming/
    └── new-dataset/
            ↓ (watcher detects)
        BIDS Validator
            ↓
        Celery Task Queue
            ↓
     [Extract → Encode → Index]
            ↓
        Elasticsearch
```

**Tech Stack:**
- Celery 5.3+
- Redis 7
- watchdog 4.0+

---

## Phase 4: Web Interface (3 weeks)

**Goal:** Non-technical user interface for clinicians

### Week 10-12: Streamlit MVP (Milestone 4.0)

**Deliverables:**
- Streamlit app in `src/neuropoly_db/web/`
- Pages:
  - **Search**: Semantic/keyword/hybrid with result table
  - **Datasets**: Browse ingested datasets with stats
  - **Upload**: ZIP upload or server path ingestion
  - **Admin**: Index management (admin users only)
- Basic authentication (streamlit-authenticator)
- Real-time ingestion progress
- Docker deployment

**Why Streamlit for MVP:**
- ✅ Python-native (reuse all existing code)
- ✅ Rapid development (< 500 lines for 80% of UI)
- ✅ Built-in authentication
- ✅ Auto-updates on code change
- ⚠️ Not suitable for high-concurrency production (use React later)

**Future (Phase 4B — Not in initial scope):**
- Custom React SPA with:
  - Advanced search interface
  - NiiVue for scan preview
  - Real-time collaboration features

---

## Timeline Summary

```
Month 1 (Weeks 1-4):   Backend Foundation
├─ W1-2: Production Docker + Security
└─ W3-4: FastAPI Backend + API

Month 2 (Weeks 5-8):   Automation
├─ W5-6: CLI Tool
└─ W7-8: Ingestion Pipeline (Part 1)

Month 3 (Weeks 9-12):  User Interface
├─ W9: Ingestion Pipeline (Part 2)
└─ W10-12: Streamlit Web UI

Total: 3 months to production MVP
```

---

## Scaling Considerations (100k+ Documents)

### Elasticsearch Sizing

**For 100,000 scans:**
- **Embeddings**: 768d × 100k × 4 bytes = ~300 MB (raw)
- **Metadata**: ~50 fields × 100k = ~200 MB (raw)
- **ES Overhead**: 2-3× for inverted indices, doc values, etc.
- **Total Index Size**: 1-2 GB on disk

**Resource Requirements:**
- **RAM**: 8 GB minimum (4-6 GB for ES heap + OS + API)
- **Disk**: 20 GB (10 GB ES data + 10 GB buffer/snapshots)
- **CPU**: 4 cores recommended

### Index Strategy

**Current (Course):** Single index `neuroimaging`  
**Production:** Dataset-based indices with alias

```bash
# Create per-dataset indices
PUT neuroimaging-ds000117
PUT neuroimaging-7t_trt
...

# Alias for unified search
POST _aliases
{
  "actions": [
    { "add": { "index": "neuroimaging-*", "alias": "neuroimaging" }}
  ]
}
```

**Benefits:**
- Independent reindexing (fix one dataset without touching others)
- Easy deletion (drop dataset = drop index)
- Shard distribution (2 shards × 50 datasets = 100 shards total)
- ILM policies per dataset

### Search Performance Targets

| Operation        | Target Latency | Notes                       |
| ---------------- | -------------- | --------------------------- |
| kNN (k=10)       | < 100ms        | With int8_hnsw quantization |
| BM25             | < 50ms         | Inverted index optimized    |
| Hybrid (RRF)     | < 200ms        | Parallel execution          |
| Concurrent users | 10-20          | With Redis caching          |

### Ingestion Throughput

- **Sequential**: ~50-100 scans/minute (embedding bottleneck)
- **Parallel (4 workers)**: ~200-400 scans/minute
- **100k scans**: 4-8 hours initial ingest (one-time)
- **Incremental**: Real-time (< 1 min latency for new datasets)

---

## Future Phases (Post-MVP)

### Phase 5: Advanced Features (4-6 weeks)
- SPLADE sparse vectors (learned sparse embeddings)
- Query expansion with neuroimaging ontology
- Learning-to-rank with user feedback
- User accounts and workspaces
- Annotation and curation tools

### Phase 6: Multi-Site & Cloud (3-4 weeks)
- Kubernetes deployment
- Cross-cluster search
- Cloud object storage (S3) for backups
- Multi-tenancy support
- Institutional SSO integration

### Phase 7: Integrations (Ongoing)
- XNAT connector
- DICOM server integration
- PACS integration
- Export to BIDS-Apps
- Git-Annex for distributed storage

---

## Risk Management

### Technical Risks

| Risk                                | Impact | Mitigation                                                             |
| ----------------------------------- | ------ | ---------------------------------------------------------------------- |
| ES performance degrades > 100k docs | High   | Implement sharding strategy, monitor query latency, optimize mappings  |
| Embedding generation bottleneck     | Medium | Parallel Celery workers, GPU acceleration option, batch processing     |
| API authentication complexity       | Medium | Start simple (API keys), design for extension (OAuth2/SAML)            |
| Solo developer bandwidth            | High   | AI-assisted development, incremental delivery, ruthless prioritization |

### Operational Risks

| Risk                             | Impact | Mitigation                                                        |
| -------------------------------- | ------ | ----------------------------------------------------------------- |
| Data corruption during ingestion | High   | Atomic operations, rollback capability, backup/restore procedures |
| ES disk full                     | Medium | Disk monitoring, ILM policies, automated cleanup                  |
| Service downtime                 | Low    | Health checks, restart policies, monitoring alerts                |

---

## Success Metrics

### Phase 1 (Backend)
- [ ] ES cluster green status with 100k test documents
- [ ] API search latency < 200ms (p95)
- [ ] 100% test coverage for search endpoints
- [ ] Zero-downtime deployment capability

### Phase 2 (CLI)
- [ ] All operations accessible via CLI
- [ ] Help documentation complete
- [ ] Configuration management working

### Phase 3 (Ingestion)
- [ ] Auto-ingest new datasets within 5 minutes
- [ ] Ingestion success rate > 99%
- [ ] Error recovery and retry working

### Phase 4 (Web UI)
- [ ] Non-technical users can search without training
- [ ] Upload and ingest workflow < 10 clicks
- [ ] Page load time < 2 seconds

---

## Decision Log

See `docs/architecture/adr/` for detailed Architecture Decision Records:

- [ADR-0001](architecture/adr/0001-fastapi-for-api-layer.md): FastAPI for API Layer
- [ADR-0002](architecture/adr/0002-celery-for-async-jobs.md): Celery for Async Jobs
- [ADR-0003](architecture/adr/0003-streamlit-mvp-then-react.md): Streamlit MVP then React
- [ADR-0004](architecture/adr/0004-scaling-strategy-100k-documents.md): Scaling Strategy for 100k Documents

---

## References

- [Development Guide](DEVELOPMENT.md)
- [Scaling Architecture](architecture/SCALING.md)
- [Deployment Guide](deployment/quickstart.md) (to be created)
- [API Documentation](../src/neuropoly_db/api/README.md) (to be created)
- [Educational Course](00-overview.md)

---

**Last Updated:** March 9, 2026  
**Next Review:** April 9, 2026 (after Phase 1 completion)

# ADR-0001: FastAPI for API Layer

**Date:** 2026-03-09  
**Status:** Accepted  
**Deciders:** Solo developer + AI assistant  
**Technical Story:** Need RESTful API for search and ingest operations

---

## Context

NeuroPoly DB needs an API layer to:
- Provide programmatic access to search functionality
- Support CLI and web UI clients
- Enable future integrations (PACS, XNAT, etc.)
- Handle async operations (ingestion jobs)
- Scale to support multiple concurrent users

The API must:
- Be easy to develop and maintain (solo developer)
- Support async/await for Elasticsearch operations
- Auto-generate documentation (OpenAPI/Swagger)
- Integrate with Python ecosystem (sentence-transformers, PyBIDS)
- Be production-ready with proper error handling

---

## Decision

We will use **FastAPI** as the web framework for the API layer.

---

## Rationale

### Why FastAPI?

**Pros:**
1. **Async Native**: Built on ASGI (Starlette), perfect for I/O-bound ES operations
2. **Type Safety**: Pydantic models provide automatic validation and serialization
3. **Auto Documentation**: OpenAPI schema generated automatically from code
4. **Performance**: One of the fastest Python frameworks (benchmarks show ~2-3× faster than Flask)
5. **Developer Experience**: 
   - Excellent error messages
   - IDE autocomplete support
   - Minimal boilerplate
6. **Ecosystem**: 
   - Large community
   - Many extensions (auth, CORS, rate limiting)
   - Works seamlessly with `elasticsearch-py` async client
7. **AI-Friendly**: Clear patterns make it easy for AI assistants to generate correct code

### Alternatives Considered

**Flask (+ Flask-RESTful):**
- ❌ Not async-native (would need threads/greenlets for ES)
- ❌ Manual OpenAPI documentation
- ❌ Less type safety
- ✅ More familiar to some developers
- ✅ Larger ecosystem (but FastAPI catching up)

**Django REST Framework:**
- ❌ Heavy framework (ORM, admin, templates not needed)
- ❌ Overhead for our use case (metadata-only, ES as data store)
- ❌ Steeper learning curve
- ✅ Excellent if we needed relational DB and admin interface

**Flask + connexion (OpenAPI-first):**
- ✅ OpenAPI schema as source of truth
- ❌ More boilerplate than FastAPI
- ❌ Less active development

---

## Example Code

```python
# src/neuropoly_db/api/routers/search.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Literal

from neuropoly_db.core.elasticsearch import get_es_client
from neuropoly_db.core.embeddings import get_encoder

router = APIRouter(prefix="/api/v1", tags=["search"])

class SearchQuery(BaseModel):
    query: str = Field(..., description="Search query text")
    mode: Literal["semantic", "keyword", "hybrid"] = Field(
        "hybrid",
        description="Search mode"
    )
    k: int = Field(10, ge=1, le=100, description="Number of results")

class SearchResult(BaseModel):
    dataset: str
    subject: str
    suffix: str
    score: float
    metadata: dict

@router.post("/search", response_model=list[SearchResult])
async def search(
    query: SearchQuery,
    client = Depends(get_es_client),
    encoder = Depends(get_encoder)
):
    """
    Universal search endpoint supporting semantic, keyword, and hybrid modes.
    
    - **semantic**: Vector similarity search using dense embeddings
    - **keyword**: BM25 full-text search  
    - **hybrid**: Combined BM25 + kNN with RRF fusion
    """
    if query.mode == "semantic":
        return await search_semantic(client, encoder, query)
    elif query.mode == "keyword":
        return await search_keyword(client, query)
    else:
        return await search_hybrid(client, encoder, query)
```

**Auto-generated OpenAPI docs at `/docs`:**
- Interactive UI to test endpoints
- Request/response schemas
- Authentication flows

---

## Consequences

### Positive

1. **Rapid Development**: Solo developer can build API quickly with AI assistance
2. **Type Safety**: Pydantic catches errors at runtime and provides clear feedback
3. **Performance**: Async operations scale well for ES queries
4. **Documentation**: OpenAPI schema is always up-to-date (generated from code)
5. **Testing**: Great support for pytest with FastAPI TestClient
6. **Future-Proof**: Easy to add authentication, rate limiting, webhooks

### Negative

1. **Learning Curve**: If unfamiliar with async/await patterns (mitigated by AI assistance)
2. **Debugging**: Async stack traces can be harder to read
3. **Ecosystem Maturity**: Some extensions less mature than Flask equivalents

### Neutral

1. **Deployment**: Requires ASGI server (uvicorn/gunicorn+uvicorn) not WSGI
2. **Dependencies**: Brings in Starlette, Pydantic (but these are lightweight)

---

## Implementation Plan

1. **Week 3**: 
   - Create FastAPI app skeleton
   - Implement `/health` and `/stats` endpoints
   - Set up OpenAPI documentation
   
2. **Week 4**:
   - Implement search endpoints (semantic, keyword, hybrid)
   - Add dataset listing endpoints
   - Write tests with TestClient

3. **Future**:
   - Add authentication middleware (API keys → OAuth2)
   - Implement rate limiting (slowapi)
   - Add request/response logging
   - Set up CORS for web UI

---

## Validation

We will validate this decision by:
- [ ] Successfully deploying a working API in Week 4
- [ ] Achieving < 200ms latency for hybrid search (p95)
- [ ] Auto-generated OpenAPI docs are complete and accurate
- [ ] CLI and web UI can consume the API without issues
- [ ] AI assistant can generate correct FastAPI code from prompts

---

## References

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [FastAPI Performance Benchmarks](https://fastapi.tiangolo.com/benchmarks/)
- [Elasticsearch Async Python Client](https://elasticsearch-py.readthedocs.io/en/latest/async.html)
- [Pydantic Documentation](https://docs.pydantic.dev/)

---

**Supersedes:** N/A (first API decision)  
**Superseded by:** N/A  
**Related:** ADR-0002 (Celery for Async Jobs)

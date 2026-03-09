---
description: Template for implementing FastAPI endpoints with proper async patterns, Pydantic validation, and error handling.
applyTo:
  - src/neuropoly_db/api/**/*.py
---

# FastAPI Endpoint Implementation

You are implementing a FastAPI endpoint for the NeuroPoly DB neuroimaging search engine.

## Context

- **API Framework**: FastAPI with async/await patterns
- **Validation**: Pydantic models for request/response
- **Elasticsearch**: Use async client from `neuropoly_db.core.elasticsearch`
- **Embeddings**: Use encoder from `neuropoly_db.core.embeddings`
- **Error Handling**: Return proper HTTP status codes with descriptive messages

## Code Structure

```python
# src/neuropoly_db/api/routers/<module>.py
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, validator
from typing import Literal, Optional

from neuropoly_db.core.elasticsearch import get_es_client
from neuropoly_db.core.embeddings import get_encoder

router = APIRouter(prefix="/api/v1", tags=["<module>"])

# Request/response models
class <Operation>Request(BaseModel):
    """Request model with validation."""
    field: str = Field(..., description="Field description", min_length=1)
    
    @validator('field')
    def validate_field(cls, v):
        # Custom validation logic
        return v

class <Operation>Response(BaseModel):
    """Response model."""
    result: dict
    metadata: dict

# Endpoint implementation
@router.post("/<path>", response_model=<Operation>Response)
async def operation_name(
    request: <Operation>Request,
    client = Depends(get_es_client),
    encoder = Depends(get_encoder)
):
    """
    Concise endpoint description.
    
    - **field**: Field explanation
    - Returns: Response explanation
    
    Raises:
        HTTPException: 404 if resource not found
        HTTPException: 400 if invalid request
        HTTPException: 500 if internal error
    """
    try:
        # Business logic here
        result = await client.search(...)
        
        return <Operation>Response(
            result=result,
            metadata={"count": len(result)}
        )
    
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found"
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error in operation_name: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )
```

## Guidelines

### Async/Await Best Practices
- Always use `async def` for endpoint functions
- Use `await` for all I/O operations (Elasticsearch, encoding)
- Don't use blocking operations (no `time.sleep()`, use `asyncio.sleep()`)
- Use `asyncio.gather()` for parallel operations

### Pydantic Validation
- Use `Field()` for parameter descriptions and constraints
- Add `@validator` for custom validation logic
- Use `Literal` for enum-like fields (e.g., `mode: Literal["hybrid", "semantic", "keyword"]`)
- Add `examples` to Field for OpenAPI documentation

### Error Handling
- Catch specific exceptions first (NotFoundError, ValidationError)
- Always have a generic Exception handler at the end
- Log errors with enough context for debugging
- Return descriptive error messages (but don't leak internal details)

### OpenAPI Documentation
- Write clear docstrings with parameter descriptions
- Use Markdown formatting in docstrings
- Add `response_model` to all endpoints
- Include example requests in Pydantic models with `Config.schema_extra`

### Testing
- Write tests using `TestClient` from FastAPI
- Cover happy path and error cases
- Mock external dependencies (Elasticsearch, encoder)

## Example: Search Endpoint

```python
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Literal, Optional
import logging

from neuropoly_db.core.elasticsearch import get_es_client
from neuropoly_db.core.embeddings import get_encoder
from neuropoly_db.core.search import hybrid_search, semantic_search, keyword_search

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["search"])

class SearchRequest(BaseModel):
    query: str = Field(
        ..., 
        description="Natural language search query",
        min_length=1,
        max_length=500,
        examples=["T1w brain scans at 3 Tesla"]
    )
    mode: Literal["hybrid", "semantic", "keyword"] = Field(
        "hybrid",
        description="Search mode: hybrid (BM25+kNN+RRF), semantic (kNN only), keyword (BM25 only)"
    )
    k: int = Field(
        10,
        ge=1,
        le=100,
        description="Number of results to return"
    )
    dataset_filter: Optional[str] = Field(
        None,
        description="Filter results to specific dataset (e.g., 'ds000001')"
    )

class SearchResult(BaseModel):
    dataset: str
    subject: str
    session: Optional[str] = None
    suffix: str
    score: float
    metadata: dict

class SearchResponse(BaseModel):
    results: list[SearchResult]
    total: int
    query_time_ms: float
    mode: str

@router.post("/search", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    client = Depends(get_es_client),
    encoder = Depends(get_encoder)
):
    """
    Universal search endpoint supporting multiple search modes.
    
    - **hybrid**: Combines BM25 keyword search with kNN vector search using RRF fusion
    - **semantic**: Vector similarity search using dense embeddings
    - **keyword**: Traditional BM25 full-text search
    
    Returns ranked results with relevance scores.
    
    Example:
        ```json
        {
          "query": "functional MRI motor task 3T",
          "mode": "hybrid",
          "k": 20
        }
        ```
    """
    import time
    start_time = time.time()
    
    try:
        # Build Elasticsearch query based on mode
        if request.mode == "hybrid":
            results = await hybrid_search(
                client=client,
                encoder=encoder,
                query=request.query,
                k=request.k,
                dataset_filter=request.dataset_filter
            )
        elif request.mode == "semantic":
            results = await semantic_search(
                client=client,
                encoder=encoder,
                query=request.query,
                k=request.k,
                dataset_filter=request.dataset_filter
            )
        else:  # keyword
            results = await keyword_search(
                client=client,
                query=request.query,
                k=request.k,
                dataset_filter=request.dataset_filter
            )
        
        query_time_ms = (time.time() - start_time) * 1000
        
        return SearchResponse(
            results=[SearchResult(**hit) for hit in results],
            total=len(results),
            query_time_ms=round(query_time_ms, 2),
            mode=request.mode
        )
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid search parameters: {str(e)}"
        )
    except ConnectionError:
        logger.error("Elasticsearch connection failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search service temporarily unavailable"
        )
    except Exception as e:
        logger.error(f"Unexpected error in search endpoint: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )
```

## Checklist

Before submitting your endpoint implementation, verify:

- [ ] Endpoint function is `async def`
- [ ] Request model uses Pydantic with `Field()` validation
- [ ] Response model is specified with `response_model`
- [ ] Docstring includes description and parameter explanations
- [ ] Error handling covers common cases (400, 404, 500, 503)
- [ ] Errors are logged with enough context
- [ ] All I/O operations use `await`
- [ ] Dependencies are injected with `Depends()`
- [ ] OpenAPI documentation is clear (`/docs` endpoint)
- [ ] Tests written with `TestClient`

## Related

- [ADR-0001: FastAPI for API Layer](../docs/architecture/adr/0001-fastapi-for-api-layer.md)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Pydantic Validation](https://docs.pydantic.dev/)

---
description: Template for implementing Celery tasks for BIDS dataset ingestion with progress tracking and error handling.
applyTo:
  - src/neuropoly_db/worker/**/*.py
---

# Celery Ingestion Task Implementation

You are implementing a Celery background task for the NeuroPoly DB neuroimaging search engine.

## Context

- **Task Queue**: Celery with Redis broker
- **BIDS Parsing**: PyBIDS for metadata extraction
- **Embeddings**: sentence-transformers with CPU batching
- **Elasticsearch**: Bulk indexing with helpers
- **Progress Tracking**: Custom task states with percentage updates
- **Error Handling**: Automatic retries with exponential backoff

## Code Structure

```python
# src/neuropoly_db/worker/tasks.py
from celery import Celery, Task
from celery.utils.log import get_task_logger
from elasticsearch.helpers import bulk
from typing import Callable, Optional

from neuropoly_db.core.ingest import parse_bids_dataset, batch_encode
from neuropoly_db.core.elasticsearch import get_blocking_client

logger = get_task_logger(__name__)

celery_app = Celery(
    "neuropoly_db",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/1"
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour
    task_soft_time_limit=3000,  # 50 min warning
)

@celery_app.task(
    bind=True,
    name="ingest_dataset",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_jitter=True
)
def ingest_dataset_task(
    self: Task,
    dataset_path: str,
    index_name: str,
    overwrite: bool = False
) -> dict:
    """
    Ingest a BIDS dataset into Elasticsearch.
    
    Args:
        dataset_path: Path to BIDS dataset directory
        index_name: Target Elasticsearch index
        overwrite: Whether to delete existing data first
    
    Returns:
        dict: Result summary with scan count and duration
    
    Raises:
        ValueError: If dataset is invalid
        ConnectionError: If Elasticsearch is unreachable
    """
    logger.info(f"Starting ingestion: {dataset_path} -> {index_name}")
    
    # Progress callback
    def update_progress(current: int, total: int, context: dict = None):
        if current % 10 == 0:  # Update every 10 scans
            self.update_state(
                state="PROGRESS",
                meta={
                    "current": current,
                    "total": total,
                    "percent": int(100 * current / total),
                    "context": context or {}
                }
            )
    
    try:
        result = ingest_dataset(
            dataset_path=dataset_path,
            index_name=index_name,
            overwrite=overwrite,
            progress_callback=update_progress
        )
        
        logger.info(f"Ingestion complete: {result['scans_indexed']} scans")
        return {
            "status": "complete",
            "scans_indexed": result["scans_indexed"],
            "duration_seconds": result["duration"]
        }
    
    except Exception as exc:
        logger.error(f"Ingestion failed: {exc}", exc_info=True)
        raise
```

## Guidelines

### Task Definition
- Use `@celery_app.task(bind=True)` to access task instance (`self`)
- Set `name` explicitly for stable task routing
- Configure `autoretry_for` for transient errors
- Set reasonable `task_time_limit` and `task_soft_time_limit`
- Use JSON serialization (not pickle) for security

### Progress Tracking
- Call `self.update_state(state="PROGRESS", meta={...})` periodically
- Update every N items (not on every item) to avoid overhead
- Include useful context: current item, total, percentage, current file
- Use consistent meta structure across tasks

### Error Handling
- Catch specific exceptions (ValueError, ConnectionError, TimeoutError)
- Log errors with `logger.error(..., exc_info=True)` for stack traces
- Use `autoretry_for` for transient failures
- Set `max_retries` and `countdown` (delay between retries)
- Let unhandled exceptions bubble up (Celery will mark as FAILURE)

### Resource Management
- Close connections properly (context managers)
- Clean up temporary files on failure
- Set memory limits if processing large files

### Logging
- Use `get_task_logger(__name__)` for proper task context
- Log start, progress milestones, and completion
- Include task parameters in log messages
- Don't log sensitive data (API keys, passwords)

## Example: BIDS Ingestion Task

```python
# src/neuropoly_db/worker/tasks.py
from celery import Celery, Task
from celery.utils.log import get_task_logger
from elasticsearch.helpers import bulk, BulkIndexError
from sentence_transformers import SentenceTransformer
import time
from pathlib import Path
from typing import Optional, Callable

from neuropoly_db.core.bids_parser import parse_bids_dataset
from neuropoly_db.core.elasticsearch import get_blocking_client

logger = get_task_logger(__name__)

celery_app = Celery(
    "neuropoly_db",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/1"
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3000,
    worker_prefetch_multiplier=1,  # One task at a time
)

# Initialize encoder once (shared across tasks in same worker)
_encoder = None

def get_encoder() -> SentenceTransformer:
    """Lazy-load encoder (heavy model, load once per worker)."""
    global _encoder
    if _encoder is None:
        logger.info("Loading sentence transformer model...")
        _encoder = SentenceTransformer("all-mpnet-base-v2", device="cpu")
    return _encoder

@celery_app.task(
    bind=True,
    name="ingest_dataset",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_jitter=True
)
def ingest_dataset_task(
    self: Task,
    dataset_path: str,
    index_name: str,
    overwrite: bool = False,
    batch_size: int = 64
) -> dict:
    """
    Ingest a BIDS dataset into Elasticsearch.
    
    Steps:
    1. Validate BIDS dataset (check dataset_description.json)
    2. Parse all NIfTI + JSON sidecar files
    3. Generate embeddings in batches
    4. Bulk index to Elasticsearch
    5. Refresh index
    
    Args:
        dataset_path: Path to BIDS dataset directory
        index_name: Target Elasticsearch index (e.g., "neuroimaging-ds000001")
        overwrite: If True, delete existing index first
        batch_size: Number of descriptions to encode at once (32-64 optimal)
    
    Returns:
        dict: {
            "status": "complete",
            "scans_indexed": int,
            "duration_seconds": float,
            "index_name": str
        }
    
    Raises:
        ValueError: If dataset is not valid BIDS
        ConnectionError: If Elasticsearch is unreachable (will retry)
        BulkIndexError: If indexing fails (will retry)
    """
    start_time = time.time()
    logger.info(f"Task {self.request.id}: Ingesting {dataset_path} -> {index_name}")
    
    # Validate input
    dataset_path = Path(dataset_path)
    if not dataset_path.exists():
        raise ValueError(f"Dataset path does not exist: {dataset_path}")
    
    if not (dataset_path / "dataset_description.json").exists():
        raise ValueError(f"Missing dataset_description.json in {dataset_path}")
    
    try:
        # Get clients
        client = get_blocking_client()
        encoder = get_encoder()
        
        # Check Elasticsearch connection
        if not client.ping():
            raise ConnectionError("Elasticsearch is not reachable")
        
        # Overwrite: delete existing index
        if overwrite and client.indices.exists(index=index_name):
            logger.info(f"Deleting existing index: {index_name}")
            client.indices.delete(index=index_name)
        
        # Step 1: Parse BIDS dataset
        logger.info("Parsing BIDS dataset...")
        self.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": 100, "percent": 5, "stage": "parsing"}
        )
        
        scans = parse_bids_dataset(dataset_path)
        total_scans = len(scans)
        
        if total_scans == 0:
            raise ValueError(f"No scans found in {dataset_path}")
        
        logger.info(f"Found {total_scans} scans")
        
        # Step 2: Build descriptions and collect metadata
        logger.info("Building descriptions...")
        descriptions = []
        documents = []
        
        for i, scan in enumerate(scans):
            desc = build_description_text(scan["metadata"])
            descriptions.append(desc)
            documents.append(scan["metadata"])
            
            # Progress update every 50 scans
            if (i + 1) % 50 == 0:
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "current": i + 1,
                        "total": total_scans,
                        "percent": int(10 + 20 * (i + 1) / total_scans),
                        "stage": "building descriptions"
                    }
                )
        
        # Step 3: Batch encode descriptions
        logger.info(f"Encoding {total_scans} descriptions (batch_size={batch_size})...")
        self.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": total_scans, "percent": 30, "stage": "encoding"}
        )
        
        embeddings = encoder.encode(
            descriptions,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True
        )
        
        logger.info(f"Encoded {len(embeddings)} embeddings")
        
        # Step 4: Prepare bulk actions
        logger.info("Preparing bulk actions...")
        actions = []
        for doc, embedding in zip(documents, embeddings):
            doc["metadata_embedding"] = embedding.tolist()
            actions.append({
                "_index": index_name,
                "_source": doc
            })
        
        # Step 5: Bulk index
        logger.info(f"Bulk indexing {len(actions)} documents...")
        self.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": total_scans, "percent": 70, "stage": "indexing"}
        )
        
        success_count, errors = bulk(
            client,
            actions,
            chunk_size=200,
            raise_on_error=False,
            stats_only=False
        )
        
        # Check for errors
        if errors:
            logger.warning(f"{len(errors)} indexing errors occurred")
            # Sample first error
            if errors[0]:
                logger.error(f"Sample error: {errors[0]}")
        
        # Refresh index
        logger.info(f"Refreshing index {index_name}...")
        client.indices.refresh(index=index_name)
        
        # Add to alias if not already present
        alias_name = "neuroimaging"
        if not client.indices.exists_alias(name=alias_name, index=index_name):
            logger.info(f"Adding index to alias {alias_name}")
            client.indices.put_alias(index=index_name, name=alias_name)
        
        duration = time.time() - start_time
        
        result = {
            "status": "complete",
            "scans_indexed": success_count,
            "duration_seconds": round(duration, 2),
            "index_name": index_name
        }
        
        logger.info(
            f"Task {self.request.id} complete: {success_count} scans indexed in {duration:.1f}s"
        )
        
        return result
    
    except (ConnectionError, TimeoutError) as exc:
        logger.error(f"Transient error (will retry): {exc}")
        raise  # Auto-retry
    
    except BulkIndexError as exc:
        logger.error(f"Bulk indexing failed: {exc}")
        raise  # Auto-retry
    
    except ValueError as exc:
        logger.error(f"Invalid dataset: {exc}")
        # Don't retry for invalid input
        raise
    
    except Exception as exc:
        logger.error(f"Unexpected error: {exc}", exc_info=True)
        raise

def build_description_text(metadata: dict) -> str:
    """
    Build searchable description text from BIDS metadata.
    
    Combines key fields into natural language text for BM25 search.
    """
    fields = []
    
    # Dataset and subject
    if "dataset" in metadata:
        fields.append(f"Dataset {metadata['dataset']}")
    if "subject" in metadata:
        fields.append(f"subject {metadata['subject']}")
    
    # Modality (suffix)
    if "suffix" in metadata:
        fields.append(f"{metadata['suffix']} scan")
    
    # Scanner details
    if "MagneticFieldStrength" in metadata:
        fields.append(f"{metadata['MagneticFieldStrength']} Tesla")
    if "Manufacturer" in metadata:
        fields.append(f"{metadata['Manufacturer']} scanner")
    
    # Sequence parameters
    if "RepetitionTime" in metadata:
        fields.append(f"TR {metadata['RepetitionTime']}s")
    if "EchoTime" in metadata:
        fields.append(f"TE {metadata['EchoTime']}s")
    
    # Task
    if "task" in metadata:
        fields.append(f"{metadata['task']} task")
    elif "TaskName" in metadata:
        fields.append(f"{metadata['TaskName']} task")
    
    return " ".join(fields)
```

## Example: Task Status Polling (API Side)

```python
# src/neuropoly_db/api/routers/ingest.py
from fastapi import APIRouter, HTTPException, status
from celery.result import AsyncResult

from neuropoly_db.worker.tasks import celery_app, ingest_dataset_task

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

@router.post("/datasets")
async def start_ingestion(
    dataset_path: str,
    index_name: str,
    overwrite: bool = False
):
    """Start background dataset ingestion."""
    # Trigger Celery task
    task = ingest_dataset_task.delay(dataset_path, index_name, overwrite)
    
    return {
        "task_id": task.id,
        "status": "queued",
        "poll_url": f"/api/v1/ingest/tasks/{task.id}"
    }

@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Get ingestion task status."""
    task = AsyncResult(task_id, app=celery_app)
    
    response = {
        "task_id": task_id,
        "state": task.state
    }
    
    if task.state == "PENDING":
        response["info"] = "Task is queued or not found"
    
    elif task.state == "PROGRESS":
        response["progress"] = task.info  # {current, total, percent, stage}
    
    elif task.state == "SUCCESS":
        response["result"] = task.result  # {status, scans_indexed, duration_seconds}
    
    elif task.state == "FAILURE":
        response["error"] = str(task.info)  # Exception message
    
    return response
```

## Running the Worker

```bash
# Development (single worker)
celery -A neuropoly_db.worker.tasks worker \
    --loglevel=info \
    --concurrency=1

# Production (auto-scaling)
celery -A neuropoly_db.worker.tasks worker \
    --loglevel=info \
    --autoscale=4,1 \
    --max-tasks-per-child=10

# Monitoring with Flower
celery -A neuropoly_db.worker.tasks flower --port=5555
```

## Checklist

Before submitting your Celery task implementation, verify:

- [ ] Task decorated with `@celery_app.task(bind=True, name="...")`
- [ ] Progress updates call `self.update_state(state="PROGRESS", meta={...})`
- [ ] Errors are logged with `logger.error(..., exc_info=True)`
- [ ] Transient errors use `autoretry_for` with backoff
- [ ] Time limits set (`task_time_limit`, `task_soft_time_limit`)
- [ ] JSON serialization used (not pickle)
- [ ] Heavy resources loaded once per worker (not per task)
- [ ] Bulk operations used for Elasticsearch (not individual inserts)
- [ ] Index refreshed after bulk indexing
- [ ] Task returns useful result summary

## Related

- [ADR-0002: Celery for Async Jobs](../docs/architecture/adr/0002-celery-for-async-jobs.md)
- [ADR-0004: Scaling Strategy](../docs/architecture/adr/0004-scaling-strategy-100k-documents.md)
- [Celery Documentation](https://docs.celeryproject.org/)
- [Elasticsearch Bulk Helpers](https://elasticsearch-py.readthedocs.io/en/latest/helpers.html)

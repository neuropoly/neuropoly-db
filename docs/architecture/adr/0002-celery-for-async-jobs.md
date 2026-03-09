# ADR-0002: Celery for Async Task Processing

**Date:** 2026-03-09  
**Status:** Accepted  
**Deciders:** Solo developer + AI assistant  
**Technical Story:** Need background task processing for BIDS ingestion at scale

---

## Context

NeuroPoly DB needs to handle long-running operations that should not block API requests:

1. **BIDS Dataset Ingestion**: 
   - Scanning 100+ NIfTI files per dataset
   - Extracting JSON sidecar metadata
   - Generating embeddings (CPU-intensive, ~50-100ms per scan)
   - Bulk indexing to Elasticsearch
   - May take 5-30 minutes per dataset

2. **Batch Operations**:
   - Re-indexing datasets when schema changes
   - Updating embeddings when switching models
   - Deleting datasets and cleaning up indices

3. **Monitoring**:
   - Need task status tracking (queued, running, complete, failed)
   - Progress reporting (e.g., "Processing scan 234/1045")
   - Retry logic for transient failures
   - Task history and logs

4. **Scaling Requirements**:
   - Solo developer start → multi-site deployment
   - Single worker → multiple workers
   - In-process queue → distributed queue

---

## Decision

We will use **Celery** with **Redis** as the message broker for distributed task processing.

---

## Rationale

### Why Celery?

**Pros:**
1. **Industry Standard**: Most widely used Python task queue (mature, battle-tested)
2. **Distributed**: Scales from single worker to many workers across machines
3. **Flexible Broker Support**: Redis (simple), RabbitMQ (robust), AWS SQS (cloud)
4. **Task Monitoring**: 
   - Flower web UI for task inspection
   - Built-in task result backend
   - Progress tracking with custom states
5. **Retry Logic**: Automatic retries with exponential backoff
6. **Priority Queues**: Route urgent tasks to fast workers
7. **Scheduling**: celery-beat for periodic tasks (future: auto-reindex, cleanup)
8. **Integration**: Works seamlessly with FastAPI via `celery.result.AsyncResult`
9. **Solo-Friendly**: Can run with single worker in dev, scale later

### Why Redis as Broker?

**Pros:**
1. **Simple Setup**: Single Docker container, no clustering needed initially
2. **Fast**: In-memory, perfect for task queue (< 1ms latency)
3. **Dual Purpose**: 
   - Message broker for Celery
   - Result backend for task status
   - Cache for API responses (future)
4. **Persistence**: RDB snapshots prevent task loss on restart
5. **Familiar**: Most devs know Redis basics

### Alternatives Considered

**FastAPI BackgroundTasks:**
- ❌ Runs in request process (dies if app restarts)
- ❌ No persistence (tasks lost on crash)
- ❌ No progress tracking
- ❌ Can't scale across machines
- ✅ Simple for trivial tasks (e.g., send email)
- **Verdict**: Not sufficient for ingestion jobs

**ARQ (async Python task queue):**
- ✅ Async-native (built on asyncio)
- ✅ Simple API
- ❌ Smaller ecosystem
- ❌ Less mature monitoring tools
- ❌ Fewer examples for complex workflows
- **Verdict**: Interesting but riskier for solo dev

**RQ (Redis Queue):**
- ✅ Simpler than Celery
- ✅ Python-native types (pickle)
- ❌ Redis-only (no RabbitMQ fallback)
- ❌ No Canvas (chains, groups, chords)
- ❌ Limited monitoring tools
- **Verdict**: Good for simple use cases, limits future flexibility

**Apache Airflow:**
- ❌ Heavy framework (overhead for our use case)
- ❌ DAG-centric (overkill for ingest jobs)
- ✅ Excellent for complex data pipelines
- **Verdict**: Over-engineered for our needs

**Kubernetes Jobs:**
- ❌ Requires K8s (not needed for single-lab start)
- ❌ Higher operational complexity
- ✅ Native if already on K8s
- **Verdict**: Future option for cloud deployment

---

## Example Code

```python
# src/neuropoly_db/worker/tasks.py
from celery import Celery, Task
from celery.utils.log import get_task_logger

from neuropoly_db.core.ingest import ingest_bids_dataset

logger = get_task_logger(__name__)

# Initialize Celery app
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
    task_time_limit=3600,  # 1 hour max
    task_soft_time_limit=3000,  # 50 min warning
)

@celery_app.task(bind=True, name="ingest_dataset")
def ingest_dataset_task(
    self: Task,
    dataset_path: str,
    index_name: str,
    overwrite: bool = False
) -> dict:
    """
    Ingest a BIDS dataset into Elasticsearch.
    
    Updates task state with progress every 10 scans.
    """
    logger.info(f"Starting ingestion: {dataset_path} -> {index_name}")
    
    # Custom progress callback
    def update_progress(current: int, total: int, file: str):
        if current % 10 == 0:
            self.update_state(
                state="PROGRESS",
                meta={
                    "current": current,
                    "total": total,
                    "percent": int(100 * current / total),
                    "current_file": file
                }
            )
    
    try:
        result = ingest_bids_dataset(
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
        logger.error(f"Ingestion failed: {exc}")
        raise self.retry(exc=exc, countdown=60, max_retries=3)


# API integration (FastAPI endpoint)
from fastapi import APIRouter, BackgroundTasks
from celery.result import AsyncResult

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

@router.post("/datasets")
async def start_ingestion(
    dataset_path: str,
    index_name: str = "neuroimaging"
):
    """Start async dataset ingestion."""
    task = ingest_dataset_task.delay(dataset_path, index_name)
    
    return {
        "task_id": task.id,
        "status": "queued",
        "poll_url": f"/api/v1/ingest/tasks/{task.id}"
    }

@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Get ingestion task status and progress."""
    task = AsyncResult(task_id, app=celery_app)
    
    if task.state == "PROGRESS":
        return {
            "task_id": task_id,
            "state": task.state,
            "progress": task.info  # {current, total, percent, current_file}
        }
    elif task.state == "SUCCESS":
        return {
            "task_id": task_id,
            "state": task.state,
            "result": task.result  # {status, scans_indexed, duration_seconds}
        }
    else:
        return {
            "task_id": task_id,
            "state": task.state,
            "info": str(task.info)  # Error message if failed
        }
```

**Running the Worker:**
```bash
# Development (single worker)
celery -A neuropoly_db.worker.tasks worker --loglevel=info

# Production (multiple workers, autoscale)
celery -A neuropoly_db.worker.tasks worker \
    --loglevel=info \
    --autoscale=4,1 \
    --max-tasks-per-child=10

# Monitoring with Flower
celery -A neuropoly_db.worker.tasks flower --port=5555
```

---

## Consequences

### Positive

1. **Reliability**: Tasks survive API restarts, retries handle transient failures
2. **Progress Tracking**: Users see real-time progress (not "still waiting?")
3. **Scalability**: 
   - Start with 1 worker
   - Add workers to same Redis queue as demand grows
   - Distribute workers across machines
4. **Developer Experience**:
   - Flower UI for debugging (http://localhost:5555)
   - Clear task logs
   - AI assistants familiar with Celery patterns
5. **Future Features**:
   - Scheduled tasks (celery-beat): nightly re-indexing, cleanup
   - Priority queues: urgent ingest jobs to fast lane
   - Task chains: ingest → validate → notify

### Negative

1. **Operational Complexity**: 
   - Another service to run (Redis)
   - Another process to monitor (Celery worker)
   - Need proper logging and alerting
2. **Debugging**: 
   - Errors happen in worker process (not API process)
   - Need centralized logging (future: ELK stack)
3. **Dependencies**: Redis must be highly available (task queue blocks if Redis down)
4. **Result Expiry**: Must configure TTL for result backend (avoid Redis bloat)

### Neutral

1. **Learning Curve**: Celery has many features (mitigated by AI assistance)
2. **Serialization**: JSON-only (no pickle) for security (limits argument types)

---

## Implementation Plan

1. **Week 5 (Backend Phase)**:
   - Add Redis to `docker-compose.yml`
   - Create `src/neuropoly_db/worker/` module
   - Define `ingest_dataset_task` with progress tracking
   - Write unit tests with `celery.contrib.testing`

2. **Week 6 (CLI Phase)**:
   - Integrate with CLI: `neuropoly-db ingest <path> --async`
   - Add `neuropoly-db tasks list` and `neuropoly-db tasks status <id>`
   - CLI polls task status and shows progress bar

3. **Week 7-8 (Ingestion Phase)**:
   - Refactor `scripts/ingest.py` to use Celery tasks
   - Add batch operations: re-index, delete dataset
   - Implement retry logic for failures
   - Add Flower for monitoring

4. **Future**:
   - Add celery-beat for scheduled tasks
   - Implement priority queues (urgent datasets)
   - Set up task result expiry (Redis TTL)
   - Add webhook notifications (task complete → notify user)

---

## Validation

We will validate this decision by:
- [ ] Successfully ingesting a 1000-scan dataset in background (Week 7)
- [ ] Task survives API restart without losing progress
- [ ] Flower UI shows real-time task status
- [ ] Failed tasks auto-retry and eventually succeed
- [ ] Can scale to 2+ workers on separate machines (Week 12)

---

## References

- [Celery Documentation](https://docs.celeryproject.org/en/stable/)
- [FastAPI + Celery Integration](https://fastapi.tiangolo.com/tutorial/background-tasks/#using-a-database-or-other-services)
- [Flower Monitoring Tool](https://flower.readthedocs.io/)
- [Redis as Celery Broker](https://docs.celeryproject.org/en/stable/getting-started/backends-and-brokers/redis.html)

---

**Supersedes:** N/A  
**Superseded by:** N/A  
**Related:** 
- ADR-0001 (FastAPI for API Layer) — API triggers Celery tasks
- ADR-0004 (Scaling Strategy) — Multiple Celery workers for high throughput

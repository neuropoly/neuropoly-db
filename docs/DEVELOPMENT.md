# Development Guide

This guide covers local development setup, workflows, testing, and conventions for NeuroPoly DB.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Development Environment](#development-environment)
- [Project Structure](#project-structure)
- [Development Workflow](#development-workflow)
- [Testing](#testing)
- [Code Style and Linting](#code-style-and-linting)
- [Working with Elasticsearch](#working-with-elasticsearch)
- [Working with Celery](#working-with-celery)
- [Common Tasks](#common-tasks)
- [Debugging](#debugging)
- [AI-Assisted Development](#ai-assisted-development)

---

## Prerequisites

### Required

- **Docker** and **Docker Compose** (for Elasticsearch, Kibana, Redis)
- **Python 3.12+**
- **Git**
- **8GB RAM minimum** (16GB recommended for production-scale testing)
- **20GB disk space** (50GB for production datasets)

### Optional

- **VS Code** with Python extension (recommended for AI assistance)
- **GitHub Copilot** (for AI-powered coding)
- **Postman** or **curl** (for API testing)

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/neuropoly/neuropoly-db.git
cd neuropoly-db

# 2. Start infrastructure (Elasticsearch, Kibana, Redis)
docker-compose up -d

# Wait for Elasticsearch to be ready
until curl -s http://localhost:9200 | grep -q "tagline"; do
    echo "Waiting for Elasticsearch..."
    sleep 2
done

# 3. Create Python virtual environment
python3.12 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 4. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 5. Verify installation
python -c "import elasticsearch, sentence_transformers; print('OK')"

# 6. Run educational notebooks (optional)
jupyter notebook notebooks/01-setup-and-ingest.ipynb
```

---

## Development Environment

### Python Environment

We use a Python virtual environment (`.venv/`) with exact dependencies in `requirements.txt`.

**Create environment:**
```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Update dependencies:**
```bash
# Add new dependency
pip install <package>

# Freeze to requirements.txt
pip freeze > requirements.txt
```

### Docker Services

The `docker-compose.yml` file defines three services:

1. **Elasticsearch 9.3**: `http://localhost:9200`
2. **Kibana 9.3**: `http://localhost:5601`
3. **Redis 7**: `localhost:6379` (for Celery)

**Start all services:**
```bash
docker-compose up -d
```

**Check service health:**
```bash
# Elasticsearch
curl http://localhost:9200

# Kibana (wait 30s after start)
curl http://localhost:5601/status

# Redis
docker exec -it neuropoly-db-redis-1 redis-cli ping
```

**View logs:**
```bash
docker-compose logs -f elasticsearch
docker-compose logs -f kibana
```

**Stop services:**
```bash
docker-compose down
```

**Reset everything (⚠️ deletes all data):**
```bash
docker-compose down -v  # Remove volumes
rm -rf .venv/           # Remove Python env
```

---

## Project Structure

```
neuropoly-db/
├── .github/
│   ├── agents/                  # Custom Copilot agents
│   │   ├── production-architect.agent.md
│   │   ├── vector-search.agent.md
│   │   └── ...
│   ├── instructions/            # File-specific instructions
│   ├── prompts/                 # AI prompt templates
│   │   ├── api-implementation.prompt.md
│   │   ├── cli-command.prompt.md
│   │   ├── ingestion-worker.prompt.md
│   │   └── streamlit-page.prompt.md
│   └── copilot-instructions.md  # Global Copilot config
│
├── data/                        # BIDS datasets (gitignored)
│   ├── ds000001/
│   ├── ds000002/
│   └── ...
│
├── docs/
│   ├── ROADMAP.md               # 3-month development plan
│   ├── DEVELOPMENT.md           # This file
│   ├── architecture/
│   │   └── adr/                 # Architecture Decision Records
│   │       ├── 0001-fastapi-for-api-layer.md
│   │       ├── 0002-celery-for-async-jobs.md
│   │       ├── 0003-streamlit-mvp-then-react.md
│   │       └── 0004-scaling-strategy-100k-documents.md
│   └── 00-overview.md to 05-next-steps.md  # Educational docs
│
├── notebooks/
│   ├── 01-setup-and-ingest.ipynb           # Educational course
│   ├── 02-keyword-search.ipynb
│   ├── 03-semantic-search.ipynb
│   └── ...
│
├── scripts/
│   └── ingest.py                # POC ingestion script (to be refactored)
│
├── src/
│   └── neuropoly_db/            # Main package (to be created)
│       ├── __init__.py
│       ├── api/                 # FastAPI application
│       │   ├── main.py          # App entry point
│       │   └── routers/         # Endpoint routers
│       │       ├── search.py
│       │       ├── datasets.py
│       │       └── ingest.py
│       ├── cli/                 # Typer CLI application
│       │   ├── main.py
│       │   └── commands/
│       │       ├── search.py
│       │       ├── ingest.py
│       │       └── tasks.py
│       ├── core/                # Core business logic
│       │   ├── elasticsearch.py # ES client factory
│       │   ├── embeddings.py    # Encoder wrapper
│       │   ├── search.py        # Search implementations
│       │   ├── bids_parser.py   # BIDS metadata extraction
│       │   └── api_client.py    # Internal API client (for CLI)
│       ├── worker/              # Celery tasks
│       │   └── tasks.py
│       └── ui/                  # Streamlit web UI
│           ├── streamlit_app.py
│           └── pages/
│               ├── 1_🔍_Search.py
│               ├── 2_📚_Datasets.py
│               └── 3_⚙️_Ingest.py
│
├── tests/                       # Test suite (to be created)
│   ├── unit/
│   ├── integration/
│   └── conftest.py
│
├── docker-compose.yml           # Development infrastructure
├── requirements.txt             # Python dependencies
├── pyproject.toml               # Project metadata (to be created)
├── .gitignore
└── README.md
```

---

## Development Workflow

### Phase-Based Development

Follow the **ROADMAP.md** phases:

1. **Phase 1 (Weeks 1-4)**: Backend API (FastAPI)
2. **Phase 2 (Weeks 5-6)**: CLI Tool (Typer)
3. **Phase 3 (Weeks 7-9)**: Ingestion Automation (Celery)
4. **Phase 4 (Weeks 10-12)**: Web UI (Streamlit)

### Branch Strategy

```bash
# Development branch
git checkout -b dev/<feature-name>

# Work on feature
git add .
git commit -m "feat: implement search endpoint"

# Merge to main when complete
git checkout main
git merge dev/<feature-name>
```

### Commit Messages

Use conventional commits:

```
feat: add hybrid search endpoint
fix: resolve Elasticsearch connection timeout
docs: update ROADMAP.md with Phase 2 details
test: add unit tests for RRF fusion
refactor: extract search logic to core module
chore: update requirements.txt
```

---

## Testing

### Manual Testing

**Test Elasticsearch:**
```bash
# Check cluster health
curl http://localhost:9200/_cluster/health?pretty

# List indices
curl http://localhost:9200/_cat/indices?v

# View index mapping
curl http://localhost:9200/neuroimaging/_mapping?pretty

# Sample search
curl -X POST http://localhost:9200/neuroimaging/_search?pretty \
  -H 'Content-Type: application/json' \
  -d '{"size": 5, "query": {"match": {"description_text": "T1w"}}}'
```

**Test API (once implemented):**
```bash
# Health check
curl http://localhost:8000/health

# Search
curl -X POST http://localhost:8000/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "T1w brain", "mode": "hybrid", "k": 10}'
```

**Test CLI (once implemented):**
```bash
# Activate venv first
source .venv/bin/activate

# Search
neuropoly-db search "T1w brain scans"

# Ingest dataset
neuropoly-db ingest /data/ds000001 --async
```

### Unit Testing (Future)

```bash
# Install test dependencies
pip install pytest pytest-asyncio pytest-cov

# Run all tests
pytest

# Run specific test file
pytest tests/unit/test_search.py

# Run with coverage
pytest --cov=neuropoly_db --cov-report=html
```

### Integration Testing (Future)

Integration tests require running services (ES, Redis):

```bash
# Start services
docker-compose up -d

# Run integration tests
pytest tests/integration/
```

---

## Code Style and Linting

### Style Guide

- **PEP 8** for Python code
- **Type hints** for function signatures
- **Docstrings** for public functions (Google style)
- **Max line length**: 100 characters

### Tools (Future)

```bash
# Install linting tools
pip install black flake8 mypy isort

# Format code
black src/ tests/

# Sort imports
isort src/ tests/

# Check linting
flake8 src/ tests/

# Type checking
mypy src/
```

---

## Working with Elasticsearch

### Index Management

**Create index with template:**
```python
from elasticsearch import Elasticsearch

client = Elasticsearch("http://localhost:9200")

# Check if index exists
if not client.indices.exists(index="neuroimaging-ds000001"):
    client.indices.create(index="neuroimaging-ds000001")

# Add to alias
client.indices.put_alias(
    index="neuroimaging-ds000001",
    name="neuroimaging"
)
```

**Delete index:**
```bash
curl -X DELETE http://localhost:9200/neuroimaging-ds000001
```

**View index stats:**
```bash
curl http://localhost:9200/_cat/indices/neuroimaging-*?v&s=index
```

### Search Debugging

**Explain query (see scoring):**
```bash
curl -X POST http://localhost:9200/neuroimaging/_search?pretty \
  -H 'Content-Type: application/json' \
  -d '{
    "explain": true,
    "size": 1,
    "query": {"match": {"description_text": "T1w brain"}}
  }'
```

**Profile query (see performance):**
```bash
curl -X POST http://localhost:9200/neuroimaging/_search?pretty \
  -H 'Content-Type: application/json' \
  -d '{
    "profile": true,
    "query": {"match": {"description_text": "T1w"}}
  }'
```

### Kibana Console

Use Kibana Dev Tools for interactive queries: http://localhost:5601/app/dev_tools#/console

```json
GET /neuroimaging/_search
{
  "size": 10,
  "query": {
    "match": {
      "description_text": "functional MRI"
    }
  }
}
```

---

## Working with Celery

### Start Worker

```bash
# Activate venv
source .venv/bin/activate

# Start worker (development)
celery -A neuropoly_db.worker.tasks worker --loglevel=info

# Start with autoscaling (production)
celery -A neuropoly_db.worker.tasks worker --loglevel=info --autoscale=4,1
```

### Monitor with Flower

```bash
# Start Flower web UI
celery -A neuropoly_db.worker.tasks flower --port=5555

# Open http://localhost:5555
```

### Task Debugging

**List active tasks:**
```bash
celery -A neuropoly_db.worker.tasks inspect active
```

**Purge all tasks:**
```bash
celery -A neuropoly_db.worker.tasks purge
```

**Test task directly:**
```python
from neuropoly_db.worker.tasks import ingest_dataset_task

# Synchronous call (blocks)
result = ingest_dataset_task("/data/ds000001", "neuroimaging-ds000001")
print(result)
```

---

## Common Tasks

### Ingest a BIDS Dataset

**Using script (POC):**
```bash
source .venv/bin/activate
python scripts/ingest.py /data/ds000001
```

**Using CLI (future):**
```bash
neuropoly-db ingest /data/ds000001 --async
```

### Run Jupyter Notebooks

```bash
source .venv/bin/activate
jupyter notebook
```

Navigate to `notebooks/` and run notebooks 01-09 sequentially.

### Add a New Dataset

1. Download BIDS dataset to `data/<dataset_id>/`
2. Verify structure: `data/<dataset_id>/dataset_description.json` exists
3. Ingest: `python scripts/ingest.py data/<dataset_id>`
4. Verify in Kibana: http://localhost:5601

### Backup/Restore Elasticsearch Data

**Backup (via snapshot):**
```bash
# Register snapshot repository
curl -X PUT http://localhost:9200/_snapshot/my_backup \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "fs",
    "settings": {
      "location": "/usr/share/elasticsearch/snapshots"
    }
  }'

# Create snapshot
curl -X PUT http://localhost:9200/_snapshot/my_backup/snapshot_1?wait_for_completion=true
```

**Restore:**
```bash
curl -X POST http://localhost:9200/_snapshot/my_backup/snapshot_1/_restore
```

**Simple export (small datasets):**
```bash
# Export index to JSON
elasticdump \
  --input=http://localhost:9200/neuroimaging \
  --output=neuroimaging-backup.json \
  --type=data
```

---

## Debugging

### Elasticsearch Issues

**Cannot connect to Elasticsearch:**
```bash
# Check if running
docker ps | grep elasticsearch

# Check logs
docker-compose logs elasticsearch

# Restart
docker-compose restart elasticsearch
```

**Out of memory errors:**
```bash
# Increase heap size in docker-compose.yml
ES_JAVA_OPTS: "-Xms8g -Xmx8g"  # 8GB heap

# Restart
docker-compose up -d elasticsearch
```

**Slow search queries:**
```bash
# Check index stats
curl http://localhost:9200/neuroimaging/_stats?pretty

# Profile query
curl -X POST http://localhost:9200/neuroimaging/_search?pretty \
  -d '{"profile": true, "query": {...}}'

# Check shard allocation
curl http://localhost:9200/_cat/shards?v
```

### Python/Dependency Issues

**Module not found:**
```bash
# Ensure venv is activated
source .venv/bin/activate

# Reinstall dependencies
pip install -r requirements.txt
```

**Version conflicts:**
```bash
# Check installed versions
pip list | grep elasticsearch

# Force reinstall
pip install --force-reinstall elasticsearch==X.X.X
```

### Celery Issues

**Worker not picking up tasks:**
```bash
# Check worker is running
celery -A neuropoly_db.worker.tasks inspect active

# Check Redis connection
docker exec -it neuropoly-db-redis-1 redis-cli ping

# Restart worker
# Ctrl+C, then restart
```

**Task stuck in PENDING:**
```bash
# Check task UUID is correct
# Check worker logs for errors
# Purge queue and retry
celery -A neuropoly_db.worker.tasks purge
```

---

## AI-Assisted Development

### Using GitHub Copilot

This project is configured for AI-assisted development with GitHub Copilot in VS Code.

**Global instructions:** `.github/copilot-instructions.md`  
**File-specific rules:** `.github/instructions/*.instructions.md`  
**Prompt templates:** `.github/prompts/*.prompt.md`  
**Custom agents:** `.github/agents/*.agent.md`

### Invoke Custom Agents

In VS Code Copilot Chat:

```
@production-architect implement the search endpoint
```

Available agents:
- `@production-architect` — Production implementation specialist
- `@vector-search` — Elasticsearch vector search expert
- `@neuroimaging-bids` — BIDS metadata expert
- `@data-science` — Data analysis and visualization
- `@debugger` — Systematic debugging

### Use Prompt Templates

When implementing features, reference prompt templates:

- **API endpoint**: Read `.github/prompts/api-implementation.prompt.md`
- **CLI command**: Read `.github/prompts/cli-command.prompt.md`
- **Celery task**: Read `.github/prompts/ingestion-worker.prompt.md`
- **Streamlit page**: Read `.github/prompts/streamlit-page.prompt.md`

These templates provide:
- Code structure patterns
- Best practices
- Error handling examples
- Checklists

### Example AI Workflow

1. **Plan**: Ask `@production-architect` to review the next task from ROADMAP.md
2. **Implement**: Generate code using prompt templates
3. **Test**: Run code, check errors
4. **Debug**: If issues, use `@debugger` agent
5. **Iterate**: Refine until working

---

## Next Steps

1. **Complete educational notebooks** (01-09) to understand the system
2. **Read ROADMAP.md** to understand the 3-month development plan
3. **Read ADRs** in `docs/architecture/adr/` to understand key decisions
4. **Start Phase 1** (Backend API) when ready to implement

---

## Questions?

- **Documentation**: See `docs/` folder
- **Architecture**: See `docs/architecture/adr/`
- **Roadmap**: See `docs/ROADMAP.md`
- **Issues**: File GitHub issue or ask `@production-architect`

Happy coding! 🚀

# Production Deployment Infrastructure

This directory contains production deployment configurations and automation scripts.

## ✅ What's Implemented

### Docker Compose Configurations

1. **`docker-compose.yml`** — Development stack (existing)
   - Elasticsearch 9.3 (no security)
   - Kibana 9.3
   - Redis 7 (newly added)
   - Ollama LLM server

2. **`docker-compose.prod.yml`** — Production stack (NEW)
   - Elasticsearch with **security enabled** (xpack, API keys)
   - 4-8GB heap allocation
   - Redis with password protection
   - Health checks and restart policies
   - Resource limits
   - Persistent volumes

### Deployment Scripts

3. **`scripts/deploy.sh`** — One-command deployment
   - Supports `--dev` and `--prod` modes
   - Pre-flight checks (Docker, vm.max_map_count)
   - Auto-generates `.env` with secure passwords
   - Waits for services to be healthy
   - Shows access URLs and credentials

4. **`scripts/backup.sh`** — Elasticsearch snapshot management
   - Setup snapshot repository
   - Create snapshots
   - List all snapshots
   - Restore from snapshot
   - Delete old snapshots

5. **`scripts/setup_index_template.py`** — Initialize index template
   - Creates `neuroimaging-template` in Elasticsearch
   - Verifies template creation
   - Shows template configuration

## 🚀 Quick Start

### Development Deployment

```bash
# Start development stack (no security)
./scripts/deploy.sh --dev

# Or use docker-compose directly
docker-compose up -d
```

Access:
- Elasticsearch: http://localhost:9200
- Kibana: http://localhost:5601
- Redis: localhost:6379

### Production Deployment

```bash
# Deploy production stack (with security)
./scripts/deploy.sh --prod
```

This will:
1. Check prerequisites (Docker, vm.max_map_count)
2. Create `.env` with secure passwords if missing
3. Pull Docker images
4. Start services with health checks
5. Wait for Elasticsearch to be ready
6. Display access URLs and credentials

**Default credentials** (generated in `.env`):
```
Username: elastic
Password: changeme_elastic_<random-hex>
```

⚠️ **IMPORTANT**: Change these passwords before deploying to production!

### Stopping Services

```bash
# Stop services (keeps data)
./scripts/deploy.sh --stop

# Restart services
./scripts/deploy.sh --restart

# Reset everything (⚠️ deletes all data)
./scripts/deploy.sh --reset
```

## 🔐 Security Configuration

### Production Mode Differences

| Feature | Development | Production |
|---------|-------------|------------|
| Security | Disabled | **Enabled** (xpack) |
| Authentication | None | API keys or basic auth |
| TLS | No | Optional (recommended) |
| Heap Size | 1GB | 4-8GB |
| Replicas | 0 | 1 |
| Resource Limits | No | Yes (Docker) |
| Data Persistence | Volume | Volume + snapshots |

### Creating an API Key

After production deployment, create an API key for the application:

```bash
# Get the password from .env
ELASTIC_PASSWORD=$(grep ELASTIC_PASSWORD .env | cut -d= -f2)

# Create API key
curl -X POST 'http://localhost:9200/_security/api_key' \
  -u "elastic:$ELASTIC_PASSWORD" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "neuropoly-db",
    "role_descriptors": {
      "neuropoly": {
        "cluster": ["all"],
        "index": [{
          "names": ["neuroimaging*"],
          "privileges": ["all"]
        }]
      }
    }
  }'
```

Save the returned API key in your `.env`:
```bash
ES_API_KEY=<API-key-from-response>
```

## 💾 Backup & Restore

### Setup Snapshot Repository

```bash
./scripts/backup.sh --setup
```

This creates a filesystem-based snapshot repository in the Elasticsearch container.

### Create Backup

```bash
# Create snapshot with auto-generated name
./scripts/backup.sh --create

# Create snapshot with custom name
./scripts/backup.sh --create backup-2026-03-09
```

Snapshots include all indices matching `neuroimaging*`.

### List Backups

```bash
./scripts/backup.sh --list
```

### Restore from Backup

```bash
./scripts/backup.sh --restore backup-2026-03-09
```

⚠️ **Warning**: This will overwrite existing data!

### Delete Old Backups

```bash
./scripts/backup.sh --delete backup-2026-03-09
```

## 📊 Resource Requirements

### Development (Local)
- **RAM**: 8GB (4GB ES, 2GB OS, 2GB Python)
- **CPU**: 4 cores
- **Disk**: 20GB
- **Scale**: 10,000-20,000 scans

### Production (Single Lab)
- **RAM**: 16GB (8GB ES, 4GB OS, 4GB workers)
- **CPU**: 8 cores
- **Disk**: 50GB SSD
- **Scale**: 50,000-100,000 scans

### Production (Multi-Site)
- **RAM**: 32GB (16GB ES, 8GB OS, 8GB workers)
- **CPU**: 16 cores
- **Disk**: 200GB NVMe SSD
- **Scale**: 500,000+ scans

### Cloud Equivalents
- **Dev**: AWS t3.large (2 vCPU, 8GB, ~$60/mo)
- **Lab**: AWS m5.xlarge (4 vCPU, 16GB, ~$140/mo)
- **Prod**: AWS m5.2xlarge (8 vCPU, 32GB, ~$280/mo)

## 🔧 Configuration Files

### `.env` (Production)

```bash
# Elasticsearch
ELASTIC_PASSWORD=<strong-password>

# Kibana
KIBANA_PASSWORD=<strong-password>

# Redis
REDIS_PASSWORD=<strong-password>

# Application (optional)
ES_API_KEY=<api-key>
```

### System Requirements

**Linux**: Set vm.max_map_count permanently
```bash
# Check current value
sysctl vm.max_map_count

# Set temporarily
sudo sysctl -w vm.max_map_count=262144

# Set permanently (survives reboot)
echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

**macOS**: Done automatically by Docker Desktop

**Windows**: Done automatically by Docker Desktop

## 📋 Health Checks

### Check Service Status

```bash
# All services
docker-compose ps

# Elasticsearch health
curl http://localhost:9200/_cluster/health?pretty

# Redis health
docker exec neuropoly-es-redis redis-cli ping

# Kibana health
curl http://localhost:5601/api/status
```

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f elasticsearch
docker-compose logs -f redis
```

### Monitor Resources

```bash
# Container stats
docker stats

# Disk usage
docker system df -v
```

## 🐛 Troubleshooting

### Elasticsearch won't start

**Check vm.max_map_count**:
```bash
sysctl vm.max_map_count  # Should be >= 262144
```

**Check logs**:
```bash
docker-compose logs elasticsearch
```

**Check disk space**:
```bash
df -h
docker system df
```

### Out of memory errors

**Increase heap size** in `docker-compose.prod.yml`:
```yaml
ES_JAVA_OPTS: "-Xms8g -Xmx8g"  # 8GB heap
```

**Check memory usage**:
```bash
docker stats neuropoly-prod-elasticsearch
```

### Redis connection refused

**Check password** (production mode):
```bash
# Get password from .env
REDIS_PASSWORD=$(grep REDIS_PASSWORD .env | cut -d= -f2)

# Test connection
docker exec neuropoly-prod-redis redis-cli --pass "$REDIS_PASSWORD" ping
```

### Cannot connect to Elasticsearch

**Check security settings**:
```bash
# Development (no auth)
curl http://localhost:9200

# Production (with auth)
curl -u elastic:<password> http://localhost:9200
```

## 📚 References

- **ROADMAP.md**: Phase 1, Weeks 1-2 (Infrastructure)
- **ADR-0004**: Scaling Strategy for 100k Documents
- [Elasticsearch Production Deployment](https://www.elastic.co/guide/en/elasticsearch/reference/current/docker.html)
- [Redis Persistence](https://redis.io/topics/persistence)

## ✨ Next Steps

1. **Test the setup**: Run `./scripts/deploy.sh --dev`
2. **Initialize index template**: Run `python scripts/setup_index_template.py`
3. **Ingest test dataset**: Use refactored ingestion pipeline
4. **Deploy to production**: Run `./scripts/deploy.sh --prod` with proper credentials

---

**Status**: ✅ Option B Complete — Production infrastructure ready for deployment

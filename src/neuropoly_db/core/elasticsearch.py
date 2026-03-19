"""
Elasticsearch client factory and index template management.

This module provides:
- Elasticsearch client initialization (sync and async)
- Index template creation for dataset-based indices
- Index alias management
- Health checks
"""

from elasticsearch import Elasticsearch, AsyncElasticsearch
from typing import Optional, Dict, Any
import logging

from neuropoly_db.core.config import settings

logger = logging.getLogger(__name__)


# ==============================================================================
# Index Template Configuration
# ==============================================================================

def get_neuroimaging_index_template() -> Dict[str, Any]:
    """
    Get the index template for neuroimaging dataset indices.
    
    This template applies to all indices matching the pattern `neuroimaging-*`.
    Each BIDS dataset gets its own index (e.g., neuroimaging-ds000001).
    
    Key features:
    - 1 primary shard per dataset (optimal for <10k docs)
    - 0 replicas in development, 1 in production
    - 768-dimensional dense vector field (int8_hnsw quantization)
    - Text field for BM25 keyword search
    - Keyword fields for filtering (dataset, subject, suffix, etc.)
    
    Returns:
        Dict containing the index template configuration
    
    References:
        - ADR-0004: Scaling Strategy for 100k Documents
        - https://www.elastic.co/guide/en/elasticsearch/reference/current/index-templates.html
    """
    return {
        "index_patterns": ["neuroimaging-*"],
        "priority": 100,
        "template": {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,  # Change to 1 in production
                "refresh_interval": "30s",  # Optimize for bulk indexing
                "codec": "best_compression",
                "max_result_window": 10000,
                "index": {
                    "knn": True,  # Enable kNN search
                }
            },
            "mappings": {
                "properties": {
                    # Dataset and subject information
                    "dataset": {
                        "type": "keyword"
                    },
                    "subject": {
                        "type": "keyword"
                    },
                    "session": {
                        "type": "keyword"
                    },
                    
                    # File information
                    "suffix": {
                        "type": "keyword"
                    },
                    "path": {
                        "type": "keyword",
                        "index": False  # Don't index, just store
                    },
                    
                    # Searchable description text (BM25)
                    "description_text": {
                        "type": "text",
                        "analyzer": "standard",
                        "fields": {
                            "keyword": {
                                "type": "keyword",
                                "ignore_above": 256
                            }
                        }
                    },
                    
                    # Dense vector embedding (768d, int8 quantization)
                    "metadata_embedding": {
                        "type": "dense_vector",
                        "dims": 768,
                        "index": True,
                        "similarity": "cosine",
                        "index_options": {
                            "type": "int8_hnsw",
                            "m": 16,
                            "ef_construction": 100
                        }
                    },
                    
                    # Scanner metadata (for filtering and display)
                    "Manufacturer": {
                        "type": "keyword"
                    },
                    "ManufacturersModelName": {
                        "type": "keyword"
                    },
                    "MagneticFieldStrength": {
                        "type": "float"
                    },
                    "RepetitionTime": {
                        "type": "float"
                    },
                    "EchoTime": {
                        "type": "float"
                    },
                    "FlipAngle": {
                        "type": "float"
                    },
                    
                    # Task information (for fMRI)
                    "task": {
                        "type": "keyword"
                    },
                    "TaskName": {
                        "type": "keyword"
                    },
                    
                    # Timestamps
                    "indexed_at": {
                        "type": "date"
                    }
                }
            }
        }
    }


# ==============================================================================
# Client Factory Functions
# ==============================================================================

def get_elasticsearch_client(
    host: Optional[str] = None,
    api_key: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    timeout: Optional[int] = None,
    max_retries: Optional[int] = None
) -> Elasticsearch:
    """
    Create a synchronous Elasticsearch client.
    
    Args:
        host: Elasticsearch host URL (default: from settings)
        api_key: API key for authentication (default: from settings)
        username: Username for basic auth (default: from settings)
        password: Password for basic auth (default: from settings)
        timeout: Request timeout in seconds (default: from settings)
        max_retries: Max retry attempts (default: from settings)
    
    Returns:
        Configured Elasticsearch client
    
    Raises:
        ConnectionError: If cannot connect to Elasticsearch
    
    Example:
        >>> client = get_elasticsearch_client()
        >>> assert client.ping(), "Elasticsearch is not reachable"
        >>> info = client.info()
        >>> print(f"Connected to ES {info['version']['number']}")
    """
    host = host or settings.es_host
    timeout = timeout or settings.es_timeout
    max_retries = max_retries or settings.es_max_retries
    
    # Build authentication
    auth_params = {}
    if api_key or settings.es_api_key:
        auth_params["api_key"] = api_key or settings.es_api_key
    elif (username or settings.es_username) and (password or settings.es_password):
        auth_params["basic_auth"] = (
            username or settings.es_username,
            password or settings.es_password
        )
    
    client = Elasticsearch(
        hosts=[host],
        request_timeout=timeout,
        max_retries=max_retries,
        retry_on_timeout=True,
        **auth_params
    )
    
    # Verify connection
    if not client.ping():
        raise ConnectionError(f"Cannot connect to Elasticsearch at {host}")
    
    logger.info(f"Connected to Elasticsearch at {host}")
    return client


async def get_async_elasticsearch_client(
    host: Optional[str] = None,
    api_key: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    timeout: Optional[int] = None,
    max_retries: Optional[int] = None
) -> AsyncElasticsearch:
    """
    Create an async Elasticsearch client for FastAPI endpoints.
    
    Args:
        Same as get_elasticsearch_client()
    
    Returns:
        Configured async Elasticsearch client
    
    Example:
        >>> async def search():
        ...     client = await get_async_elasticsearch_client()
        ...     result = await client.search(index="neuroimaging", body={...})
        ...     await client.close()
    """
    host = host or settings.es_host
    timeout = timeout or settings.es_timeout
    max_retries = max_retries or settings.es_max_retries
    
    # Build authentication
    auth_params = {}
    if api_key or settings.es_api_key:
        auth_params["api_key"] = api_key or settings.es_api_key
    elif (username or settings.es_username) and (password or settings.es_password):
        auth_params["basic_auth"] = (
            username or settings.es_username,
            password or settings.es_password
        )
    
    client = AsyncElasticsearch(
        hosts=[host],
        request_timeout=timeout,
        max_retries=max_retries,
        retry_on_timeout=True,
        **auth_params
    )
    
    # Verify connection
    if not await client.ping():
        await client.close()
        raise ConnectionError(f"Cannot connect to Elasticsearch at {host}")
    
    logger.info(f"Connected to Elasticsearch (async) at {host}")
    return client


# ==============================================================================
# Index Template Management
# ==============================================================================

def create_index_template(client: Elasticsearch, name: str = "neuroimaging-template") -> bool:
    """
    Create or update the neuroimaging index template.
    
    Args:
        client: Elasticsearch client
        name: Template name (default: "neuroimaging-template")
    
    Returns:
        True if template was created/updated successfully
    
    Example:
        >>> client = get_elasticsearch_client()
        >>> create_index_template(client)
        True
    """
    template = get_neuroimaging_index_template()
    
    try:
        client.indices.put_index_template(name=name, body=template)
        logger.info(f"Index template '{name}' created/updated successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to create index template: {e}")
        raise


def ensure_alias_exists(
    client: Elasticsearch,
    index: str,
    alias: Optional[str] = None
) -> bool:
    """
    Add an index to the unified neuroimaging alias if not already present.
    
    Args:
        client: Elasticsearch client
        index: Index name (e.g., "neuroimaging-ds000001")
        alias: Alias name (default: from settings)
    
    Returns:
        True if index was added to alias (or already present)
    
    Example:
        >>> client = get_elasticsearch_client()
        >>> ensure_alias_exists(client, "neuroimaging-ds000001")
        True
    """
    alias = alias or settings.default_index_alias
    
    try:
        # Check if alias already points to this index
        if client.indices.exists_alias(name=alias, index=index):
            logger.debug(f"Index '{index}' already in alias '{alias}'")
            return True
        
        # Add index to alias
        client.indices.put_alias(index=index, name=alias)
        logger.info(f"Added index '{index}' to alias '{alias}'")
        return True
        
    except Exception as e:
        logger.error(f"Failed to add index to alias: {e}")
        raise


# ==============================================================================
# Health Check Functions
# ==============================================================================

def check_elasticsearch_health(client: Elasticsearch) -> Dict[str, Any]:
    """
    Check Elasticsearch cluster health and return status information.
    
    Args:
        client: Elasticsearch client
    
    Returns:
        Dictionary with health information
    
    Example:
        >>> client = get_elasticsearch_client()
        >>> health = check_elasticsearch_health(client)
        >>> print(f"Status: {health['status']}")
        >>> print(f"Nodes: {health['number_of_nodes']}")
    """
    try:
        # Cluster health
        health = client.cluster.health()
        
        # Cluster info
        info = client.info()
        
        # Index stats
        indices = client.cat.indices(format="json")
        neuroimaging_indices = [
            idx for idx in indices 
            if idx["index"].startswith("neuroimaging-")
        ]
        
        return {
            "status": health["status"],
            "cluster_name": health["cluster_name"],
            "number_of_nodes": health["number_of_nodes"],
            "number_of_data_nodes": health["number_of_data_nodes"],
            "active_shards": health["active_shards"],
            "elasticsearch_version": info["version"]["number"],
            "indices_count": len(neuroimaging_indices),
            "total_docs": sum(int(idx.get("docs.count", 0) or 0) for idx in neuroimaging_indices)
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise

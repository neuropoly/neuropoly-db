#!/usr/bin/env python3
"""
Initialize Elasticsearch index template for NeuroPoly DB.

This script:
1. Connects to Elasticsearch
2. Creates the neuroimaging index template
3. Verifies the template was created successfully

Usage:
    python scripts/setup_index_template.py
    
Environment variables:
    ES_HOST: Elasticsearch host (default: http://localhost:9200)
    ES_API_KEY: API key for authentication (optional)
    ES_USERNAME: Username for basic auth (optional)
    ES_PASSWORD: Password for basic auth (optional)
"""

import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from neuropoly_db.core.elasticsearch import (
    get_elasticsearch_client,
    create_index_template,
    get_neuroimaging_index_template
)
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Main setup function."""
    logger.info("NeuroPoly DB - Index Template Setup")
    logger.info("=" * 60)
    
    try:
        # Connect to Elasticsearch
        logger.info("Connecting to Elasticsearch...")
        client = get_elasticsearch_client()
        
        # Get cluster info
        info = client.info()
        logger.info(f"Connected to Elasticsearch {info['version']['number']}")
        logger.info(f"Cluster: {info['cluster_name']}")
        
        # Create index template
        logger.info("\nCreating index template...")
        template = get_neuroimaging_index_template()
        
        logger.info(f"Template pattern: {template['index_patterns']}")
        logger.info(f"Primary shards: {template['template']['settings']['number_of_shards']}")
        logger.info(f"Replica shards: {template['template']['settings']['number_of_replicas']}")
        logger.info(f"Vector dimensions: {template['template']['mappings']['properties']['metadata_embedding']['dims']}")
        
        success = create_index_template(client, name="neuroimaging-template")
        
        if success:
            logger.info("\n✓ Index template created successfully!")
            
            # Verify template exists
            templates = client.indices.get_index_template(name="neuroimaging-template")
            logger.info(f"✓ Template verified: {templates['index_templates'][0]['name']}")
            
            logger.info("\nNext steps:")
            logger.info("1. Ingest a BIDS dataset (creates index matching template)")
            logger.info("2. Index will automatically use this template")
            logger.info("3. Verify with: curl http://localhost:9200/_cat/indices/neuroimaging-*?v")
        else:
            logger.error("Failed to create index template")
            sys.exit(1)
            
    except ConnectionError as e:
        logger.error(f"Cannot connect to Elasticsearch: {e}")
        logger.error("Make sure Elasticsearch is running: docker-compose up -d")
        sys.exit(1)
    
    except Exception as e:
        logger.error(f"Setup failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

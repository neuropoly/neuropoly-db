"""
Configuration management for NeuroPoly DB.

Loads settings from environment variables with sensible defaults.
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Elasticsearch Configuration
    es_host: str = "http://localhost:9200"
    es_timeout: int = 120
    es_max_retries: int = 3

    # Security (for production)
    es_api_key: Optional[str] = None
    es_username: Optional[str] = None
    es_password: Optional[str] = None

    # Embedding Model
    embedding_model: str = "all-mpnet-base-v2"
    embedding_device: str = "cpu"  # "cuda" if GPU available

    # Index Settings
    default_index_alias: str = "neuroimaging"
    index_prefix: str = "neuroimaging-"

    # Resource Limits
    bulk_chunk_size: int = 200
    encoding_batch_size: int = 64

    # API Settings (for future use)
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Global settings instance
settings = Settings()

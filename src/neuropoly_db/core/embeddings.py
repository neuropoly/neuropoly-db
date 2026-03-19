"""
Sentence transformer encoder wrapper for generating embeddings.

This module provides a singleton encoder instance to avoid loading the model
multiple times, which is expensive (~500MB model, 5-10 seconds to load).
"""

from sentence_transformers import SentenceTransformer
import numpy as np
from typing import Union, List
import logging
import os

from neuropoly_db.core.config import settings

logger = logging.getLogger(__name__)

# Global encoder instance (loaded once)
_encoder: SentenceTransformer = None


def get_encoder(
    model_name: str = None,
    device: str = None
) -> SentenceTransformer:
    """
    Get or create the sentence transformer encoder.
    
    The encoder is loaded once and cached globally to avoid reloading.
    
    Args:
        model_name: Model name (default: from settings)
        device: Device to use ("cpu" or "cuda", default: from settings)
    
    Returns:
        SentenceTransformer encoder instance
    
    Example:
        >>> encoder = get_encoder()
        >>> embedding = encoder.encode("T1w brain scan at 3 Tesla")
        >>> print(embedding.shape)  # (768,)
    """
    global _encoder
    
    if _encoder is None:
        model_name = model_name or settings.embedding_model
        device = device or settings.embedding_device
        
        # Force CPU if CUDA not available
        if device == "cuda" and not os.environ.get("CUDA_VISIBLE_DEVICES"):
            device = "cpu"
            logger.warning("CUDA requested but not available, using CPU")
        
        logger.info(f"Loading sentence transformer: {model_name} on {device}")
        _encoder = SentenceTransformer(model_name, device=device)
        logger.info(f"Encoder loaded successfully (embedding_dim={_encoder.get_sentence_embedding_dimension()})")
    
    return _encoder


def encode_text(
    text: Union[str, List[str]],
    normalize: bool = True,
    batch_size: int = None,
    show_progress: bool = False
) -> np.ndarray:
    """
    Encode text to dense vector embeddings.
    
    Args:
        text: Single string or list of strings to encode
        normalize: Whether to normalize embeddings (recommended for cosine similarity)
        batch_size: Batch size for encoding (default: from settings)
        show_progress: Show progress bar for large batches
    
    Returns:
        Numpy array of shape (embedding_dim,) for single text or 
        (n_texts, embedding_dim) for multiple texts
    
    Example:
        >>> # Single text
        >>> emb = encode_text("T1w brain scan")
        >>> print(emb.shape)  # (768,)
        
        >>> # Multiple texts (batched)
        >>> texts = ["T1w scan", "T2w scan", "fMRI bold"]
        >>> embs = encode_text(texts, batch_size=32)
        >>> print(embs.shape)  # (3, 768)
    """
    encoder = get_encoder()
    batch_size = batch_size or settings.encoding_batch_size
    
    embeddings = encoder.encode(
        text,
        batch_size=batch_size,
        show_progress_bar=show_progress,
        normalize_embeddings=normalize,
        convert_to_numpy=True
    )
    
    return embeddings


def encode_query(query: str) -> np.ndarray:
    """
    Encode a search query to a dense vector.
    
    Convenience wrapper around encode_text() for single queries.
    
    Args:
        query: Search query text
    
    Returns:
        Normalized embedding vector of shape (768,)
    
    Example:
        >>> query_vector = encode_query("functional MRI motor task")
        >>> # Use for kNN search in Elasticsearch
    """
    return encode_text(query, normalize=True, show_progress=False)


def encode_batch(
    texts: List[str],
    batch_size: int = None,
    show_progress: bool = True
) -> np.ndarray:
    """
    Encode a batch of texts efficiently.
    
    Optimized for bulk ingestion pipelines.
    
    Args:
        texts: List of texts to encode
        batch_size: Batch size (default: from settings)
        show_progress: Show progress bar
    
    Returns:
        Numpy array of shape (n_texts, 768)
    
    Example:
        >>> descriptions = [build_description(scan) for scan in scans]
        >>> embeddings = encode_batch(descriptions, batch_size=64)
    """
    return encode_text(
        texts,
        normalize=True,
        batch_size=batch_size,
        show_progress=show_progress
    )

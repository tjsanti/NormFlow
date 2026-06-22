"""Semantic search index — re-export from mapping_service for backward compat."""

from .mapping_service import _SemanticIndex as SemanticIndex

__all__ = ["SemanticIndex"]

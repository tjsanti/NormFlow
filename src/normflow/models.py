"""NormFlow domain models — re-exported from mapping_service for backward compat."""

from .mapping_service import _ExampleMapping as ExampleMapping
from .mapping_service import _Suggestion as Suggestion

__all__ = ["ExampleMapping", "Suggestion"]

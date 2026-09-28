"""Canonical historical-news archive; independent from Batch 3 live storage."""

from .models import NormalizedArticle
from .storage import NewsArchive

__all__ = ["NewsArchive", "NormalizedArticle"]

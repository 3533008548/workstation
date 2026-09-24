"""按场景隔离的检索门面（阶段 4）。"""

from .rrf import rrf_merge
from .service import RetrievalResult, RetrievalService
from .source import Hit, RetrievalSource

__all__ = [
    "Hit",
    "RetrievalResult",
    "RetrievalService",
    "RetrievalSource",
    "rrf_merge",
]

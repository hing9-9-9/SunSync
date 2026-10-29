"""Experimental GK2A point-DSR adapter compatibility boundary.

The parser is retained while its raw transport DTOs remain in ``adapters``.
This public path keeps all experimental callers out of the production package.
"""

from app.nowcast.adapters import Gk2aDsrAdapter, Gk2aDsrError

__all__ = ["Gk2aDsrAdapter", "Gk2aDsrError"]

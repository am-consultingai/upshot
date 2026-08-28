"""Suites registered by later phases.

Kept in its own module so ``app.selftest`` stays free of heavy imports at module
scope; every suite here imports what it needs inside the function body.
"""

from __future__ import annotations

# Suite modules register themselves on import.

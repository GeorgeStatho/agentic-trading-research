"""Compatibility wrapper for ``processing.scoring``.

Prefer importing from ``processing.scoring`` in new code.
Keep this file as a backward-compatible shim only.
"""

from processing.scoring import (
    compute_directness_score,
    compute_evidence_score,
    compute_factuality_score,
    confirmation_score,
    recency_score,
)


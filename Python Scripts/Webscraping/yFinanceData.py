"""Compatibility wrapper for ``market_data.yFinanceData``.

Prefer importing from ``market_data.yFinanceData`` in new code.
Keep this file as a backward-compatible shim only.
"""

import json

from market_data.yFinanceData import *  # noqa: F401,F403


if __name__ == "__main__":
    print(json.dumps(GetSectorInfo("basic-materials"), indent=2, sort_keys=True))

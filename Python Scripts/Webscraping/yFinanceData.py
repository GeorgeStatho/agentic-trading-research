import json

from market_data.yFinanceData import *  # noqa: F401,F403


if __name__ == "__main__":
    print(json.dumps(GetSectorInfo("basic-materials"), indent=2, sort_keys=True))

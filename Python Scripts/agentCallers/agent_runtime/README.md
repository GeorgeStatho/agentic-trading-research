# agent_runtime

This folder contains smaller runtime scripts, smoke tests, and older experiments.

These files are not the main production path of the repo. The primary runtime path is:

- [Python Scripts/main.py](../../main.py)
- [Python Scripts/agentCallers/main.py](../main.py)

## Contents

### `init_agents.py`

Older experiment file that instantiates named role-based clients. Useful mostly as historical scaffolding, not as the main pipeline entrypoint.

### `market_health.py`

Small model smoke test / experiment script.

### `researcher.py`

Simple local experiment that sends a test prompt through the shared model helper.

## Status

This folder is mostly for:

- local testing
- debugging
- historical experiments

If you are trying to understand the current system, start with:

- [../main.py](../main.py)
- [../agent_stages](../agent_stages)
- [../agent_helpers](../agent_helpers)

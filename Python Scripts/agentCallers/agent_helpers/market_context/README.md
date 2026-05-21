# market_context package

This package owns the live market/account context assembly used by the manager stage.

Public entrypoint:

- `from agent_helpers.market_context import build_market_context`

Current structure:

- `__init__.py`
  Stable facade and orchestration layer.
- `common.py`
  Shared Alpaca/yfinance imports, constants, and low-level helpers.
- `equities.py`
  Current stock price, sector ETF, and market index snapshots.
- `underlying.py`
  Alpaca stock-bar history and underlying historical volatility.
- `option_chain.py`
  Option contract lookup, snapshot normalization, and option-market assembly.
- `iv_history.py`
  Persisted IV history, DTE-bucket IV percentiles, and contract IV annotations.
- `account.py`
  Buying power and matching position-state assembly.
- `__main__.py`
  Package smoke-test entrypoint for reference-price checks.

High-level flow:

1. Build the current stock-price snapshot.
2. Resolve the reference stock price used for option filtering.
3. Load underlying stock bars and compute historical volatility.
4. Load option contracts and option chain snapshots.
5. Build volatility summary and enrich it with persisted IV percentile context.
6. Load account buying power and matching stock/option positions.
7. Return a JSON-safe payload for the manager stage.

Notes:

- Alpaca is the primary live-data provider.
- `yfinance` is still used for market indices and sector ETF snapshots.
- Tests patch the facade package directly, so `__init__.py` intentionally keeps some wrappers around IV-history internals.

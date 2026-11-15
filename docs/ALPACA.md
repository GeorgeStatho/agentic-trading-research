# Alpaca in this codebase

Read this before writing code that calls Alpaca. Most broker and market-data operations already have a
wrapper here; reuse it instead of calling the SDK directly. The SDK is `alpaca-py` (pinned in
`constraints.txt`); its source in `site-packages` is the reference for request and response types.

## Where each capability lives

| Capability | Use | Notes |
| --- | --- | --- |
| Credentials and clients | `trading_support/clients.py`: `get_default_trading_client()`, `get_stock_history_client()`, `get_option_history_client()` | Keys are `PUBLIC_KEY` / `PRIVATE_KEY`, paper flag `ALPACA_PAPER` (`AlpacaSettings`, `services/core/trading_config.py`) |
| Trading operations | `services/trading/trading_gateway.py` `AlpacaTradingGateway` | Orders (single, multi-leg, stock, sell market/limit, close), order status, cancel, order history, buying power, open option positions and exposure. Construct it as `AlpacaTradingGateway(paper=..., logger=...)`, then `create_client()` |
| Market clock / is the market open | `AlpacaTradingGateway.get_market_clock()` / `market_is_open()` | Follows Alpaca's calendar, so holidays and early closes are handled. `market_is_open()` returns `False` when the call fails; use `get_market_clock()` and treat `None` as unknown when "closed" would permit something risky |
| Latest option quote (exits) | `trading_support/_option_positions_market.py` `_get_latest_option_quote()` | Returns bid, ask and mid |
| Latest stock price (exits) | `_option_positions_market.py` `_get_latest_stock_price()` | |
| Stock ATR for volatility-scaled exits | `_option_positions_market.py` `_get_underlying_atr_pct()` | See the known issues below |
| Option contracts and chains (entries) | `agentCallers/agent_helpers/market_context/option_chain.py`: `build_bucket_chains()`, `_build_option_market_snapshot()` | Contract search, snapshots with greeks, strike windows |
| Stock, index and sector ETF snapshots | `agent_helpers/market_context/equities.py` | |
| Daily stock bars | `agent_helpers/market_context/underlying_bars.py` `_fetch_daily_stock_bars()` | Requests `DataFeed.IEX` |
| Option IV and chain for an open position | `services/options/position_exit_policy.py` | |
| Closing marks for research | `services/research/research_marks.py` `fetch_latest_option_quotes()` | |
| Historical 1-minute stock and option bars | `Data/market_db_modules/backtest_alpaca_bars.py` `fetch_alpaca_minute_bars()` | Stored once per session for backtests; `run_due_minute_bar_fetch()` schedules it |
| Follower paper accounts | `services/trading/follower_fleet.py` | One `AlpacaTradingGateway` per account, with its own keys |
| News (Benzinga via Alpaca) | `Webscraping/APIs/alpacaNewsAPI.py` | |
| Start-up check | `services/runtime/startup_readiness.py` | Reads the clock and the account |
| Stock orders (legacy helper) | `trading_support/stock_orders.py` `StockTrades` | |

Paths under `Python Scripts/` are given from that root, which is an import root (`docs/DEVELOPMENT.md`).

## Data feeds and what they mean

- **Option quotes name no feed.** Alpaca serves OPRA only to subscribed accounts; others get the
  "indicative" feed, whose quotes "are not actual OPRA quotes". The DB does not record which feed was used.
  A check on 2026-11-15 found 66 of 70 real trades printed inside the recorded bid–ask, so the quotes track
  the market, but treat spreads as Alpaca's view, not the exchange's.
- **Latest stock quotes name no feed either**, so a free plan gets IEX (one exchange). Around the open these
  can be wrong: GM's snapshot on 2026-11-11 showed $93.98 when it traded between $87.97 and $88.92.
- **Historical stock bars from SIP are free once 15 minutes old.** `backtest_alpaca_bars.py` requests
  `DataFeed.SIP` and only completed sessions.
- **There are no historical option bid/ask quotes**, only option bars and trades (data since February 2024).
  Option bars are trade prices and are sparse for thin contracts; a contract may trade a handful of times a day.
- **Paper fills are simulated** and never appear in Alpaca's trade data.

## Rate limits

The basic plan allows 200 requests a minute (`tests/live/test_rate_limits.py` measures one production cycle
against it; `ALPACA_RATE_LIMIT_PER_MINUTE` overrides the limit there). Trading has priority: background fetches
(for example the scheduled minute-bar fetch) only start while `get_market_clock()` says the market is closed
and the open is more than 30 minutes away.

## SDK behaviour worth knowing

- Request models (`StockBarsRequest`, `OptionBarsRequest`, ...) turn time-zone-aware `start`/`end` into naive
  UTC. Pass aware UTC times; compare against naive UTC in test fakes.
- Bar responses are `BarSet` objects; `response.data[symbol]` is the list of bars, each with `timestamp`,
  `open`, `high`, `low`, `close`, `volume`, `trade_count` and `vwap`. Bar timestamps are the bar's start.
- Historical data calls follow `next_page_token` until the last page (up to `limit` when one is set), and
  every client retries HTTP 429 and 504 three times (`alpaca/common/rest.py`, `common/constants.py`).

## Known issues (as of 2026-11-15)

- **The stock ATR is never available in production.** None of the 14,252 stored position observations has
  `underlying_atr_pct`, so production stops are never volatility-scaled, while the backtest scales them by up
  to 2x. Not yet diagnosed.
- The option position state never stored an entry stock price until 2026-11-15, so the stop-loss's 1.5%
  underlying check never ran before then. It is now seeded from `position_outcomes`.

## Safety

- Tests and verification never place orders. `tests/live/` refuses broker writes and hosts other than
  Alpaca and Yahoo.
- Code that only reads market data says so and never imports order paths (see the docstring of
  `backtest_alpaca_bars.py`).

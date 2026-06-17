# Technical Indicators Implementation

This document describes how technical indicators are currently implemented inside `agent_helpers.market_context` and how they flow into the manager-stage market payload.

## Purpose

The technical indicators are computed as part of the underlying stock history snapshot. They are intended to provide:

- confirmation context
- timing context
- overextension/risk context
- relative-strength context versus the market and sector

They are not intended to be a standalone trade thesis source. The prompts were updated separately so the LLM treats them as a secondary filter after the research/news thesis.

## Where The Logic Lives

- `underlying.py`
  Compatibility façade and orchestration for underlying price history assembly.
- `underlying_bars.py`
  Alpaca daily-bar fetch, normalization, and bar-field extraction.
- `underlying_indicators.py`
  Technical-indicator math and output snapshot construction.
- `underlying_technical_summary.py`
  Deterministic interpretation layer that converts raw indicator values into normalized labels.

## Data Flow

1. `build_market_context(...)` in `market_context/__init__.py` builds the live stock snapshot first.
2. It resolves a `reference_stock_price` from the live quote/trade snapshot.
3. It resolves the sector ETF snapshot and passes its ticker into underlying-history assembly.
4. `underlying._build_underlying_price_history_snapshot(...)` fetches daily Alpaca bars for the stock.
5. `underlying._summarize_underlying_price_history(...)` extracts:
   - closes
   - OHLC values
   - volume
   - latest daily VWAP if present
6. It computes:
   - `historical_volatility_20d`
   - `historical_volatility_60d`
   - `technical_indicators`
   - `technical_summary`
7. The result is stored at:

```json
market_context["underlying_price_history"]["technical_indicators"]
```

and:

```json
market_context["underlying_price_history"]["technical_summary"]
```

## Payload Location

Technical indicators live under:

```json
{
  "underlying_price_history": {
    "technical_indicators": { ... },
    "technical_summary": { ... }
  }
}
```

This preserves the existing top-level `market_context` shape while adding richer stock-specific timing/risk context.

## Deterministic Summary Layer

The raw indicators are now paired with a deterministic interpretation block:

```json
"technical_summary": {
  "available": true,
  "trend_state": "bullish",
  "momentum_state": "positive_not_overheated",
  "vwap_state": "above_vwap",
  "volume_confirmation": "strong",
  "relative_strength_confirmation": "strong",
  "overextension_risk": "medium",
  "call_entry_quality": "good",
  "put_entry_quality": "poor",
  "technical_confirmation_for_calls": "moderate",
  "technical_confirmation_for_puts": "weak",
  "recommended_llm_adjustment": "approve_if_news_and_options_confirm",
  "warnings": [
    "VWAP is daily-bar VWAP, not true intraday VWAP"
  ]
}
```

This block exists so the LLM sees stable labels before it sees raw indicator values. It is intended to reduce interpretation drift across runs.

## Implemented Indicators

### 1. VWAP / Price vs VWAP

Source:

- latest daily bar `vwap` field if Alpaca provides it

Output:

```json
"vwap": {
  "available": true,
  "value": 123.45,
  "price_vs_vwap": 1.23,
  "price_vs_vwap_pct": 1.01
}
```

Behavior:

- uses `reference_price` when available
- otherwise falls back to the last close
- only computed when both `reference_price` and daily `vwap` are present

Important limitation:

- this is daily-bar VWAP context, not true intraday streaming VWAP

### 2. Relative Volume

Output:

```json
"relative_volume": {
  "available": true,
  "window_days": 20,
  "latest_volume": 1500000,
  "average_volume_20d": 1000000,
  "relative_volume_20d": 1.5
}
```

Behavior:

- latest volume is compared with the average of the prior 20 daily volumes
- the current bar is excluded from the average baseline

### 3. Price vs 20 EMA

Output:

```json
"price_vs_20ema": {
  "available": true,
  "price": 123.45,
  "ema_20": 120.10,
  "absolute_gap": 3.35,
  "percent_gap": 2.79
}
```

Behavior:

- uses a standard exponential moving average over closes
- current comparison price is the live `reference_price` when available, else last close

### 4. Price vs 50 SMA

Output:

```json
"price_vs_50sma": {
  "available": true,
  "price": 123.45,
  "sma_50": 118.80,
  "absolute_gap": 4.65,
  "percent_gap": 3.91
}
```

Behavior:

- uses a standard simple moving average over closes

### 5. RSI 14

Output:

```json
"rsi_14": {
  "available": true,
  "value": 62.4
}
```

Behavior:

- calculated from close-to-close changes
- uses smoothed average gain/loss logic after the initial 14-bar seed
- returns `100.0` if average loss is zero

### 6. MACD Histogram

Output:

```json
"macd": {
  "available": true,
  "macd_line": 1.12,
  "signal_line": 0.88,
  "histogram": 0.24
}
```

Behavior:

- fast EMA: 12
- slow EMA: 26
- signal EMA: 9
- histogram = `macd_line - signal_line`

### 7. ATR Overextension

Output:

```json
"atr_overextension": {
  "available": true,
  "atr_14": 2.15,
  "baseline_ema_20": 120.10,
  "price_distance": 3.35,
  "atr_multiple": 1.56,
  "threshold_atr": 2.0,
  "is_overextended": false,
  "direction": "above"
}
```

Behavior:

- ATR window: 14
- true range uses:
  - `high - low`
  - `abs(high - previous_close)`
  - `abs(low - previous_close)`
- overextension is measured relative to the 20 EMA, not prior close
- default threshold is `2.0 ATR`

Interpretation:

- positive `atr_multiple` means price is above the 20 EMA
- negative `atr_multiple` means price is below the 20 EMA
- `is_overextended` becomes `true` once `abs(atr_multiple) >= 2.0`

### 8. Relative Strength vs SPY

Output:

```json
"relative_strength": {
  "vs_spy": {
    "available": true,
    "benchmark_symbol": "SPY",
    "label": "SPY",
    "lookback_days": [20, 50],
    "price_ratio": 0.245,
    "ratio_sma_20": 0.239,
    "ratio_vs_sma_20_pct": 2.51,
    "returns": {
      "20d": {
        "stock_return_pct": 6.0,
        "benchmark_return_pct": 2.0,
        "relative_return_pct": 4.0
      },
      "50d": {
        "stock_return_pct": 12.0,
        "benchmark_return_pct": 5.0,
        "relative_return_pct": 7.0
      }
    },
    "error": ""
  }
}
```

Behavior:

- fetches additional daily bars for `SPY`
- aligns stock and benchmark histories to the shorter comparable length
- computes:
  - current price ratio
  - 20-day SMA of the ratio series
  - percent difference between current ratio and ratio SMA
  - 20d and 50d stock returns vs benchmark returns

### 9. Relative Strength vs Sector ETF

Output structure matches `vs_spy`, but uses the resolved company sector ETF ticker when available.

Behavior:

- `build_market_context(...)` resolves the sector ETF first
- the ETF ticker is passed into underlying history assembly
- if no sector ETF is available, `vs_sector_etf` remains unavailable with empty benchmark metadata

## Historical Volatility Still Included

Technical indicators were added without removing the existing volatility context:

- `historical_volatility_20d`
- `historical_volatility_60d`
- `recent_closes`

Those remain part of `underlying_price_history` and are still used by option-market volatility context.

## Reference Price Behavior

Many technical comparisons use `reference_price` instead of blindly using the last daily close.

That reference price is resolved earlier from live stock data and prefers:

1. latest trade price
2. midpoint when spread is tight
3. fallback quote-derived price fields

This makes the technical snapshot better aligned with current market state when the manager runs intraday.

## Availability And Fallback Rules

Each indicator block has its own `available` flag.

General pattern:

- if the required history or fields do not exist, the indicator remains unavailable
- the payload stays JSON-safe
- missing data does not fail the whole `underlying_price_history` snapshot unless no valid closes exist at all

For relative-strength specifically:

- benchmark fetches only happen in live underlying snapshot assembly
- test-only summary helpers can run without forcing SPY/sector fetches

## Design Choice: Compatibility Façade

`underlying.py` remains as a compatibility façade even after the split into `underlying_bars.py` and `underlying_indicators.py`.

Reason:

- existing tests patch helpers like `underlying._fetch_daily_stock_bars`
- the façade preserves those patch points
- internal logic is still modularized behind the façade

## Current Limitations

- VWAP is daily-bar VWAP, not streaming intraday VWAP
- relative strength currently compares against `SPY` and sector ETF, not `QQQ`
- moving averages and RSI are based on daily closes only
- ATR overextension is measured relative to 20 EMA, not multiple baselines
- no separate trend-state classification is produced; only raw/derived values are returned

## Intended LLM Usage

The prompts were updated so these indicators should be used as:

- confirmation
- timing
- overextension filter
- risk adjustment

They should not be used as:

- a standalone reason to open a trade
- a reason to ignore poor IV, spread, liquidity, or break-even quality

## Related Files

- `agent_helpers/market_context/underlying.py`
- `agent_helpers/market_context/underlying_bars.py`
- `agent_helpers/market_context/underlying_indicators.py`
- `agent_stages/strategist_prompt.py`
- `agent_stages/manager_prompt.py`
- `agent_stages/manager_normalization.py`

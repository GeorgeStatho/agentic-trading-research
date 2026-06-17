# Technical Indicators Improvement Plan

This document describes how the current technical-indicator implementation can be improved inside the market-context pipeline.

The current implementation already adds useful stock-level context under:

```json
market_context["underlying_price_history"]["technical_indicators"]
```

It includes VWAP, relative volume, price versus moving averages, RSI, MACD, ATR overextension, relative strength versus SPY, relative strength versus the sector ETF, and historical volatility.

The next improvement should not be adding many more raw indicators immediately. The higher-value improvement is adding a deterministic interpretation layer that converts raw indicator values into stable labels the LLM and risk manager can use consistently.

---

## Current Strengths

The current implementation has a strong foundation:

* Technical indicators are separated into `underlying_indicators.py`.
* Alpaca daily-bar fetching and normalization are separated into `underlying_bars.py`.
* `underlying.py` remains as a compatibility façade for existing tests.
* Each indicator block has an `available` flag.
* Missing indicator data does not break the whole market-context payload.
* Reference price prefers live quote/trade data instead of blindly using the last close.
* Technical indicators are scoped under `underlying_price_history`, preserving the existing market-context shape.
* The prompts instruct the LLM to treat indicators as secondary confirmation rather than standalone trade signals.

This is a good first version.

---

## Main Problem To Solve

Right now the payload gives the LLM raw and semi-derived values, but the LLM still has to infer the final meaning.

For example, the LLM has to decide:

```text
Is this bullish?
Is the entry overextended?
Is this a good call setup?
Should confidence be reduced?
Should the bot wait instead of buying?
```

This can lead to inconsistent behavior across model runs.

The preferred improvement is to add a deterministic summary layer that classifies the technical setup before the LLM sees it.

---

## Recommended Improvement: Add `technical_summary`

Add a new derived block next to the raw indicators:

```json
{
  "underlying_price_history": {
    "technical_indicators": {
      "...": "raw indicator blocks"
    },
    "technical_summary": {
      "available": true,
      "trend_state": "bullish",
      "momentum_state": "positive_not_overheated",
      "vwap_state": "above_vwap",
      "volume_confirmation": "strong",
      "relative_strength_confirmation": "strong",
      "overextension_risk": "medium",
      "call_entry_quality": "acceptable",
      "put_entry_quality": "poor",
      "technical_confirmation_for_calls": "moderate",
      "technical_confirmation_for_puts": "weak",
      "recommended_llm_adjustment": "approve_if_news_and_options_confirm",
      "warnings": [
        "VWAP is daily-bar VWAP, not true intraday VWAP"
      ]
    }
  }
}
```

This lets deterministic code do the basic interpretation, while the LLM uses the result as a secondary filter.

---

## Why This Is Better

Raw indicators are useful for debugging and backtesting, but they are not ideal as the only input to the LLM.

For example, this raw payload:

```json
{
  "rsi_14": {
    "available": true,
    "value": 78.2
  },
  "atr_overextension": {
    "available": true,
    "atr_multiple": 1.8,
    "is_overextended": false
  },
  "price_vs_20ema": {
    "available": true,
    "percent_gap": 5.4
  }
}
```

should probably be interpreted as:

```json
{
  "momentum_state": "hot",
  "overextension_risk": "medium",
  "entry_quality": "chase_risk"
}
```

If this interpretation is left entirely to the LLM, the bot may be inconsistent.

A deterministic summary layer makes the system:

* easier to audit
* easier to backtest
* easier to normalize
* less dependent on prompt wording
* safer for options trading
* more consistent across model runs

---

## Proposed New File

Add a new file:

```text
agent_helpers/market_context/underlying_technical_summary.py
```

Purpose:

```text
Convert raw technical indicators into stable, normalized technical labels.
```

Example function:

```python
def build_technical_summary(indicators: dict) -> dict:
    """
    Build deterministic technical interpretation labels from raw indicator blocks.

    The summary should be used by the LLM as a secondary confirmation/risk layer.
    It should not create a standalone trade thesis.
    """
```

Then call it from the existing underlying history summary flow after `technical_indicators` is computed.

---

## Suggested Summary Fields

### Top-Level Availability

```json
"available": true
```

Should be `false` if there is not enough indicator data to produce a useful summary.

---

### Trend State

```json
"trend_state": "bullish"
```

Allowed values:

```text
bullish
mixed
bearish
unknown
```

Suggested logic:

```python
if price_vs_20ema_pct > 0 and price_vs_50sma_pct > 0:
    trend_state = "bullish"
elif price_vs_20ema_pct < 0 and price_vs_50sma_pct < 0:
    trend_state = "bearish"
else:
    trend_state = "mixed"
```

Future improvement:

* Add 20 EMA slope.
* Add 50 SMA slope.
* Distinguish between “above rising EMA” and “above falling EMA.”

---

### Momentum State

```json
"momentum_state": "positive_not_overheated"
```

Allowed values:

```text
positive_not_overheated
hot
overheated
mixed
weak
unknown
```

Suggested logic:

```python
if 50 <= rsi <= 70 and macd_histogram > 0:
    momentum_state = "positive_not_overheated"
elif 70 < rsi < 78 and macd_histogram > 0:
    momentum_state = "hot"
elif rsi >= 78:
    momentum_state = "overheated"
elif rsi < 45 and macd_histogram < 0:
    momentum_state = "weak"
else:
    momentum_state = "mixed"
```

Important:

RSI above 70 should not automatically reject a bullish trade. Strong stocks can stay strong. The risk increases when high RSI is combined with ATR extension, high IV, or a large gap above VWAP/EMA.

---

### VWAP State

```json
"vwap_state": "above_vwap"
```

Allowed values:

```text
above_vwap
near_vwap
below_vwap
unknown
```

Suggested logic:

```python
if price_vs_vwap_pct > 0.25:
    vwap_state = "above_vwap"
elif price_vs_vwap_pct < -0.25:
    vwap_state = "below_vwap"
else:
    vwap_state = "near_vwap"
```

Important limitation:

The current VWAP is daily-bar VWAP from Alpaca, not true intraday streaming VWAP.

Because of that, the bot should not infer:

* VWAP reclaim
* VWAP rejection
* VWAP pullback hold
* intraday VWAP support

unless intraday bars are later added.

The summary should include a warning when VWAP is used:

```json
"warnings": [
  "VWAP is daily-bar VWAP, not true intraday VWAP"
]
```

---

### Volume Confirmation

```json
"volume_confirmation": "strong"
```

Allowed values:

```text
strong
normal
weak
unknown
```

Suggested logic:

```python
if relative_volume_20d >= 1.5:
    volume_confirmation = "strong"
elif relative_volume_20d >= 1.0:
    volume_confirmation = "normal"
else:
    volume_confirmation = "weak"
```

Use this as a confirmation layer for news-driven trades.

A bullish news event with strong relative volume is more convincing than bullish news with weak volume.

---

### Relative Strength Confirmation

```json
"relative_strength_confirmation": "strong"
```

Allowed values:

```text
strong
mixed_positive
weak
unknown
```

Suggested logic:

```python
spy_relative_20d = indicators["relative_strength"]["vs_spy"]["returns"]["20d"]["relative_return_pct"]
sector_relative_20d = indicators["relative_strength"]["vs_sector_etf"]["returns"]["20d"]["relative_return_pct"]

if spy_relative_20d > 0 and sector_relative_20d > 0:
    relative_strength_confirmation = "strong"
elif spy_relative_20d > 0 or sector_relative_20d > 0:
    relative_strength_confirmation = "mixed_positive"
else:
    relative_strength_confirmation = "weak"
```

Future improvement:

Add QQQ as another benchmark, especially because the bot often trades large technology and AI-related names.

---

### Overextension Risk

```json
"overextension_risk": "medium"
```

Allowed values:

```text
low
medium
high
unknown
```

Suggested logic:

```python
if abs(atr_multiple) >= 2.0:
    overextension_risk = "high"
elif abs(atr_multiple) >= 1.25:
    overextension_risk = "medium"
else:
    overextension_risk = "low"
```

Upgrade the risk if RSI is very high:

```python
if rsi >= 78 and atr_multiple > 1.25:
    overextension_risk = "high"
```

Interpretation:

* `low`: entry is not stretched by ATR.
* `medium`: stock may still be tradable, but avoid aggressive sizing.
* `high`: high chase risk; prefer waiting, reducing size, longer DTE, or skipping.

---

### Call Entry Quality

```json
"call_entry_quality": "acceptable"
```

Allowed values:

```text
excellent
good
acceptable
poor
chase_risk
unknown
```

Suggested logic:

Start with a simple score.

Positive points:

```text
trend_state == bullish: +2
momentum_state == positive_not_overheated: +2
vwap_state == above_vwap: +1
volume_confirmation == strong: +2
relative_strength_confirmation == strong: +2
overextension_risk == low: +2
overextension_risk == medium: +1
```

Negative points:

```text
trend_state == bearish: -3
momentum_state == weak: -2
momentum_state == overheated: -2
vwap_state == below_vwap: -2
volume_confirmation == weak: -2
relative_strength_confirmation == weak: -2
overextension_risk == high: -3
```

Example mapping:

```python
if overextension_risk == "high" and momentum_state in {"hot", "overheated"}:
    call_entry_quality = "chase_risk"
elif score >= 8:
    call_entry_quality = "excellent"
elif score >= 6:
    call_entry_quality = "good"
elif score >= 3:
    call_entry_quality = "acceptable"
elif score >= 0:
    call_entry_quality = "poor"
else:
    call_entry_quality = "poor"
```

---

### Put Entry Quality

```json
"put_entry_quality": "poor"
```

This is useful even if the bot mostly buys calls.

Suggested logic is the inverse of call quality:

Positive for puts:

```text
trend_state == bearish
momentum_state == weak
vwap_state == below_vwap
relative_strength_confirmation == weak
```

Caution:

A high RSI/overextended bullish setup does not automatically mean a good put setup. It may only mean “bad call entry.”

That distinction is important.

---

### Technical Confirmation For Calls

```json
"technical_confirmation_for_calls": "moderate"
```

Allowed values:

```text
strong
moderate
mixed
weak
bearish
unknown
```

Suggested interpretation:

```text
strong:
  bullish trend, positive momentum, strong volume, strong relative strength, low/medium overextension

moderate:
  mostly bullish but one or two caution signals

mixed:
  conflicting signals

weak:
  little confirmation for a bullish trade

bearish:
  trend, momentum, and relative strength are against calls
```

---

### Recommended LLM Adjustment

```json
"recommended_llm_adjustment": "approve_if_news_and_options_confirm"
```

Allowed values:

```text
approve_if_news_and_options_confirm
reduce_confidence
reduce_size
wait_for_better_entry
skip_unless_exceptional_catalyst
```

Suggested mapping:

```python
if technical_confirmation_for_calls == "strong" and call_entry_quality in {"excellent", "good"}:
    recommended_llm_adjustment = "approve_if_news_and_options_confirm"
elif call_entry_quality == "acceptable":
    recommended_llm_adjustment = "approve_if_news_and_options_confirm"
elif call_entry_quality == "chase_risk":
    recommended_llm_adjustment = "wait_for_better_entry"
elif technical_confirmation_for_calls == "weak":
    recommended_llm_adjustment = "reduce_confidence"
elif technical_confirmation_for_calls == "bearish":
    recommended_llm_adjustment = "skip_unless_exceptional_catalyst"
```

---

## Example Improved Payload

```json
{
  "underlying_price_history": {
    "technical_indicators": {
      "vwap": {
        "available": true,
        "value": 123.45,
        "price_vs_vwap_pct": 1.01
      },
      "relative_volume": {
        "available": true,
        "relative_volume_20d": 1.5
      },
      "price_vs_20ema": {
        "available": true,
        "percent_gap": 2.79
      },
      "price_vs_50sma": {
        "available": true,
        "percent_gap": 3.91
      },
      "rsi_14": {
        "available": true,
        "value": 62.4
      },
      "macd": {
        "available": true,
        "histogram": 0.24
      },
      "atr_overextension": {
        "available": true,
        "atr_multiple": 1.56,
        "is_overextended": false
      }
    },
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
  }
}
```

---

## Manager Prompt Improvement

The manager prompt should tell the LLM to prefer `technical_summary` over raw indicators when available.

Suggested prompt addition:

```text
If `underlying_price_history.technical_summary` is available, use it as the primary technical interpretation layer. Use raw `technical_indicators` only for supporting details.

Technical indicators are secondary context. They may confirm, weaken, delay, or resize a trade, but they must not create a standalone trade thesis.

Do not approve a trade based only on RSI, MACD, VWAP, moving averages, or relative strength.

For bullish call trades, prefer setups where:
- technical_confirmation_for_calls is strong or moderate
- call_entry_quality is excellent, good, or acceptable
- overextension_risk is low or medium
- recommended_llm_adjustment does not suggest waiting or skipping
- option pricing, IV, spread, liquidity, and break-even are acceptable

Reduce confidence, reduce size, wait, or skip if:
- call_entry_quality is poor or chase_risk
- overextension_risk is high
- technical_confirmation_for_calls is weak or bearish
- recommended_llm_adjustment suggests waiting or skipping

Never allow a bullish technical setup to override poor option pricing.
```

---

## Manager Output Schema Improvement

Add explicit technical judgment fields to the manager response.

Example:

```json
{
  "decision": "trade_candidate",
  "confidence": "medium",
  "expected_stock_direction": "up",
  "preferred_option_direction": "call",

  "technical_confirmation": "moderate",
  "entry_quality": "acceptable",
  "overextension_risk": "medium",
  "technical_action_adjustment": "approve",

  "options_pricing_risk": "low",
  "final_action_adjustment": "normal_size",

  "reasoning": {
    "research_thesis": "...",
    "technical_read": "...",
    "options_pricing_read": "...",
    "risk_read": "..."
  }
}
```

Recommended allowed values:

```text
technical_confirmation:
  strong
  moderate
  mixed
  weak
  bearish
  unknown

entry_quality:
  excellent
  good
  acceptable
  poor
  chase
  unknown

overextension_risk:
  low
  medium
  high
  unknown

technical_action_adjustment:
  approve
  reduce_confidence
  reduce_size
  wait
  skip
```

---

## Manager Normalization Improvement

Update `manager_normalization.py` so technical fields are normalized and safe.

Example:

```python
technical_confirmation = normalize_enum(
    raw.get("technical_confirmation"),
    allowed={"strong", "moderate", "mixed", "weak", "bearish", "unknown"},
    default="unknown",
)

entry_quality = normalize_enum(
    raw.get("entry_quality"),
    allowed={"excellent", "good", "acceptable", "poor", "chase", "unknown"},
    default="unknown",
)

overextension_risk = normalize_enum(
    raw.get("overextension_risk"),
    allowed={"low", "medium", "high", "unknown"},
    default="unknown",
)

technical_action_adjustment = normalize_enum(
    raw.get("technical_action_adjustment"),
    allowed={"approve", "reduce_confidence", "reduce_size", "wait", "skip"},
    default="approve",
)
```

Then downstream deterministic code can apply consequences.

Example:

```python
if technical_action_adjustment == "skip":
    decision = "do_not_trade"

elif technical_action_adjustment == "wait":
    decision = "do_not_trade"
    reason = "technical_entry_timing_wait"

elif technical_action_adjustment == "reduce_size":
    position_size_multiplier *= 0.5

if overextension_risk == "high":
    position_size_multiplier *= 0.5
```

Important:

The LLM should output labels. Deterministic code should apply the sizing and skip rules.

---

## Recommended Implementation Order

### Phase 1: Add Deterministic Summary

Add:

```text
agent_helpers/market_context/underlying_technical_summary.py
```

Implement:

```python
build_technical_summary(indicators: dict) -> dict
```

Then include the result under:

```json
market_context["underlying_price_history"]["technical_summary"]
```

This is the highest-value improvement.

---

### Phase 2: Update Manager Prompt

Update `manager_prompt.py` so the manager:

* prefers `technical_summary` when available
* treats indicators as secondary context
* outputs normalized technical labels
* does not approve trades based only on indicators
* does not ignore poor options pricing because technicals are bullish

---

### Phase 3: Update Manager Normalization

Update `manager_normalization.py` to normalize:

* `technical_confirmation`
* `entry_quality`
* `overextension_risk`
* `technical_action_adjustment`

This keeps model output safe and predictable.

---

### Phase 4: Apply Deterministic Risk Consequences

Use normalized fields to adjust behavior.

Suggested rules:

```text
technical_action_adjustment == skip:
  reject trade

technical_action_adjustment == wait:
  reject current entry and mark as wait-for-entry

technical_action_adjustment == reduce_size:
  reduce size multiplier

overextension_risk == high:
  reduce size multiplier

entry_quality == chase:
  prefer wait or skip

technical_confirmation == bearish:
  reject bullish call trades unless catalyst is exceptional
```

---

### Phase 5: Add QQQ Relative Strength

Add QQQ as another benchmark, especially for technology-heavy names.

Current relative strength:

```text
SPY
sector ETF
```

Recommended addition:

```text
QQQ
```

This is useful because many large-cap technology names may outperform SPY but still underperform QQQ.

---

### Phase 6: Add Indicator Slopes

Add:

```text
20 EMA slope
MACD histogram slope
VWAP slope, if intraday VWAP is later implemented
```

Useful distinction:

```text
MACD histogram positive and rising = improving momentum
MACD histogram positive but falling = momentum fading
```

---

### Phase 7: Add True Intraday VWAP Later

Current VWAP is daily-bar VWAP.

True intraday VWAP would require intraday bars such as 1-minute or 5-minute candles.

Only after intraday data is added should the bot infer:

* VWAP reclaim
* VWAP rejection
* VWAP pullback hold
* price holding above VWAP intraday
* price breaking below VWAP intraday

Until then, VWAP should be treated as rough daily/session context.

---

## Indicators Not Worth Adding Yet

Avoid adding too many overlapping indicators.

Do not prioritize:

* stochastic oscillator
* CCI
* Williams %R
* many extra moving averages
* candlestick pattern names
* Bollinger Bands, unless specifically used for overextension research

The current indicator set is already enough for a strong version one.

The next improvement should be classification, not indicator bloat.

---

## Best Final Architecture

Recommended flow:

```text
raw market data
↓
technical indicators
↓
deterministic technical summary
↓
LLM uses summary as secondary filter
↓
manager outputs normalized technical labels
↓
normalizer validates labels
↓
risk manager / selector applies deterministic consequences
```

Avoid this flow:

```text
raw indicators
↓
LLM decides everything
```

The first architecture is safer, more testable, and easier to backtest.

---

## Bottom Line

The current technical-indicator implementation is a strong first version.

The most important improvement is:

```text
Add a deterministic `technical_summary` layer and require the Manager to output normalized technical judgment fields.
```

This will make the bot:

* more consistent
* easier to debug
* easier to backtest
* safer for options trading
* less dependent on LLM interpretation
* better at avoiding overextended call entries

The trading strategy
The system buys directional single-leg options on individual US stocks, driven by news. It is paper-traded on Alpaca. The model decides whether to trade and in which direction. Plain Python code (the same inputs always give the same result) decides which contract, how much, and when to exit.

1. Finding ideas from the news
news_collector scrapes news and sorts it from the top down: world/macro news → sectors → industries → companies. Each level is classified by a cheap Flash-Lite model ("opportunist" stages). The top-ranked sectors and industries, optionally limited to recent news (PIPELINE_RANKING_MAX_AGE_DAYS), produce the list of candidate companies.

2. Two model decisions per company
Strategist (Gemini Flash) decides buy or do_not_buy. It writes a thesis, confidence, expected direction, time horizon, risks and contradictions, and it must cite specific stored articles.
Manager (Gemini Pro) decides call, put or neither given current conditions. The prompt in manager_prompt.py makes technicals a risk filter, not a source of ideas:
It wants agreement with VWAP, trend, relative volume and relative strength.
It penalizes overextended moves (high RSI, a move already larger than ATR) and high implied volatility (IV percentile).
It favors neither when evidence is weak, stale or contradictory.
It also picks a time horizon, which maps to a DTE (days to expiration) bucket.
3. Fixed checks before entry
Evidence-continuation gate: the idea needs grounded evidence plus confirmation from the daily trend (evidence_continuation_gate.py).
Market-regime gate: blocks trades that go against the broad market, weighted by whether the catalyst is systemic, sector-level or company-specific. It defaults to shadow mode (logs only).
Liquidity and spread: the bid/ask spread must be at most 6% of price, and quotes must be fresh with enough size.
Option hurdle: the expected move must be large enough to beat the option's premium.
Portfolio limits:
at most 5 open option positions at once
a $2,000 daily option-loss limit
one position per correlated group of stocks (cluster), and at most 25% exposure per cluster
a 7-day re-entry window per symbol
No new entries on Fridays (New York time), to avoid holding through the weekend.
4. Choosing the contract
The manager's horizon maps to a DTE bucket (option_dte_buckets.py). The code then picks the contract with the strike closest to a slightly out-of-the-money target:

Bucket	Strike target (OTM)	Take profit	Stop loss	Forced exit at
12–20 DTE	0.75%	+35%	−25%	8 DTE
20–30 DTE	1.25%	+55%	−30%	12 DTE
30–45 DTE (default)	2.5%	+70%	−35%	18 DTE
45–60 DTE	3.0%	+85%	−40%	27 DTE
Position size defaults to 1 contract (AGENT_OPTION_ORDER_QTY).

5. Managing exits
A separate option_manager loop checks open positions (_option_positions_strategy.py):

Take profit, stop loss and forced exit use the bucket thresholds above. The global fallbacks are +25% and −20%.
Stop-loss filters: a stop can require several consecutive breaches. It can also require the underlying stock to have actually moved against the position, so swings in the option price alone don't trigger it.
Trailing profit: protection starts at +20% gain. Part of the position is sold at set triggers, and the profit floor ratchets up (+60% locked in at +100%, +100% at +150%, +140% at +200%). A momentum-fade exit also applies.
Exits are limit orders. Stale pending exits are cancelled after 10 minutes.
6. Research layer (not trading yet)
Backtests, forward evaluation and the "Decision Autopsy" ledger compare variants such as strategist-only, strategist+manager, sentiment+volume and sentiment+trend (research_contract.py:74). Their results feed back into prompts as calibration. The newer discovery and investigation phases (Research Phases 2–3) are off by default and never place orders.

In one sentence: the strategy is short-to-medium-term, news-catalyst momentum, expressed through slightly out-of-the-money calls or puts. Confirmation from technicals is required, and fixed rules control entry, sizing and exits.
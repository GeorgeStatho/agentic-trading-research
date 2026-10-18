# The trading strategy

The system buys directional single-leg options on individual US stocks, driven by news, and paper-trades them
on Alpaca. Language models decide *whether* to trade and *in which direction*. Plain Python code, where the
same inputs always give the same result, decides which contract, how much and when to exit.

This public version describes the mechanisms. The tuned values (thresholds, limits and the decision
heuristics in the prompts) are kept private.

## 1. Finding ideas in the news

`news_collector` gathers news and sorts it from the top down: world and macro news, then sectors, industries
and companies. A cheap model classifies each level (the "opportunist" stages). The top-ranked sectors and
industries, optionally limited to recent news, produce the list of candidate companies. A discovery pipeline
can also propose companies from SEC filings; it runs in shadow unless it is switched on.

## 2. Two model decisions per company

- **Strategist.** Decides `trade_candidate`, `watchlist` or `do_not_trade`. It writes a thesis, confidence,
  expected direction, time horizon, catalysts, risks and contradictions, and every claim must cite stored
  articles or evidence. The citations are checked in code.
- **Manager.** Given the strategist's thesis and the live market and account context, decides `call`, `put`
  or `neither`, and picks a time horizon that maps to a days-to-expiry (DTE) bucket. Technical indicators and
  option pricing act as a risk filter on the thesis, not as a source of ideas.

The exact input each stage saw is frozen at decision time, so any decision can be replayed later.

## 3. Checks before entry

Deterministic checks run after the manager, among them:

- an evidence-continuation gate (grounded evidence plus confirmation from the daily trend);
- a market-regime gate that weighs the broad market against how systemic the catalyst is;
- liquidity, spread and quote-freshness checks on the option;
- an option hurdle: the expected move must be large enough to beat the premium;
- portfolio limits on open positions, daily loss, exposure per correlated group and re-entry per symbol;
- no new entries on Fridays (New York time), to avoid holding through the weekend.

New gates start in **shadow mode**: they record what they would have blocked without blocking it. A gate is
enforced only after a pre-registered promotion proof shows that it helps on real outcomes.

## 4. Choosing the contract

The manager's horizon maps to a DTE bucket. Each bucket has its own slightly out-of-the-money strike target
and its own take-profit, stop-loss and forced-exit levels, and the code picks the listed contract closest to
that target. Position size is fixed per order.

## 5. Managing exits

A separate `option_manager` loop watches open positions:

- take profit, stop loss and a forced exit before expiry, from the bucket's levels;
- stop-loss filters, so a swing in the option price alone does not trigger a stop: a stop can require several
  consecutive breaches and a real move against the position in the underlying stock;
- trailing profit protection that sells part of the position at set gains and ratchets a profit floor upward,
  plus a momentum-fade exit;
- limit-order exits, with stale pending exits cancelled.

## 6. Measuring it

Backtests, LLM replays of past decisions, decision autopsies, expectancy by cohort, decision attribution and
follower paper accounts (spreads, in-the-money, longer expiry, shares) measure what works. Results feed back
as calibration, and research features never place orders.

In one sentence: short-to-medium-term, news-catalyst momentum, expressed through slightly out-of-the-money
calls or puts, with technicals as confirmation and fixed rules controlling entry, sizing and exits.

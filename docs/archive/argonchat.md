Optimizing Agentic Options Trading Strategy
https://gemini.google.com/app/fd08fecd8b904f59

User prompt: I've been working on an "agentic" trading experiment that involves trading options. I've seen some success with it so far, but I believe there are some things that could be improved about it. Could you help find some general ideas on how to improve it/ make it more consistent? The trading strategy The system buys directional single-leg options on individual US stocks, driven by news. It is paper-traded on Alpaca. The model decides whether to trade and in which direction. Plain Python code (the same inputs always give the same result) decides which contract, how much, and when to exit. 1. Finding ideas from the news news_collector scrapes news and sorts it from the top down: world/macro news → sectors → industries → companies. Each level is classified by a cheap Flash-Lite model ("opportunist" stages). The top-ranked sectors and industries, optionally limited to recent news (PIPELINE_RANKING_MAX_AGE_DAYS), produce the list of candidate companies. 2. Two model decisions per company Strategist (Gemini Flash) decides buy or do_not_buy. It writes a thesis, confidence, expected direction, time horizon, risks and contradictions, and it must cite specific stored articles. Manager (Gemini Pro) decides call, put or neither given current conditions. The prompt in manager_prompt.py makes technicals a risk filter, not a source of ideas: It wants agreement with VWAP, trend, relative volume and relative strength. It penalizes overextended moves (high RSI, a move already larger than ATR) and high implied volatility (IV percentile). It favors neither when evidence is weak, stale or contradictory. It also picks a time horizon, which maps to a DTE (days to expiration) bucket. 3. Fixed checks before entry Evidence-continuation gate: the idea needs grounded evidence plus confirmation from the daily trend (evidence_continuation_gate.py). Market-regime gate: blocks trades that go against the broad market, weighted by whether the catalyst is systemic, sector-level or company-specific. It defaults to shadow mode (logs only). Liquidity and spread: the bid/ask spread must be at most 6% of price, and quotes must be fresh with enough size. Option hurdle: the expected move must be large enough to beat the option's premium. Portfolio limits: at most 5 open option positions at once a $2,000 daily option-loss limit one position per correlated group of stocks (cluster), and at most 25% exposure per cluster a 7-day re-entry window per symbol No new entries on Fridays (New York time), to avoid holding through the weekend. 4. Choosing the contract The manager's horizon maps to a DTE bucket (option_dte_buckets.py). The code then picks the contract with the strike closest to a slightly out-of-the-money target: Bucket  Strike target (OTM) Take profit Stop loss   Forced exit at 12–20 DTE   0.75%   +35%    −25%    8 DTE 20–30 DTE   1.25%   +55%    −30%    12 DTE 30–45 DTE (default) 2.5%    +70%    −35%    18 DTE 45–60 DTE   3.0%    +85%    −40%    27 DTE Position size defaults to 1 contract (AGENT_OPTION_ORDER_QTY). 5. Managing exits A separate option_manager loop checks open positions (_option_positions_strategy.py): Take profit, stop loss and forced exit use the bucket thresholds above. The global fallbacks are +25% and −20%. Stop-loss filters: a stop can require several consecutive breaches. It can also require the underlying stock to have actually moved against the position, so swings in the option price alone don't trigger it. Trailing profit: protection starts at +20% gain. Part of the position is sold at set triggers, and the profit floor ratchets up (+60% locked in at +100%, +100% at +150%, +140% at +200%). A momentum-fade exit also applies. Exits are limit orders. Stale pending exits are cancelled after 10 minutes. 6. Research layer (not trading yet) Backtests, forward evaluation and the "Decision Autopsy" ledger compare variants such as strategist-only, strategist+manager, sentiment+volume and sentiment+trend (research_contract.py:74). Their results feed back into prompts as calibration. The newer discovery and investigation phases (Research Phases 2–3) are off by default and never place orders. In one sentence: the strategy is short-to-medium-term, news-catalyst momentum, expressed through slightly out-of-the-money calls or puts. Confirmation from technicals is required, and fixed rules control entry, sizing and exits.
Response: Your architecture already gets the hardest design principle right: strict neuro-symbolic separation. Using LLMs to synthesize unstructured news and evaluate qualitative context while restricting contract math, risk gates, and order execution to deterministic Python prevents the hallucinated sizing and strike errors that sink most agentic trading systems.
To make your returns more consistent, the highest-leverage improvements lie in removing hidden volatility biases in your deterministic rules (Sections 3–5) and tightening information hygiene in your agent pipeline (Sections 1, 2, and 6).
1. Contract Selection & Sizing (Highest Impact on Consistency)
Right now, your deterministic contract selector and sizing rules introduce large, unintended variance across different stocks:

Current Rule
Why It Hurts Consistency
Concrete Upgrade
Fixed % OTM Strikes (0.75% to 3.0% by DTE bucket)
Ignores underlying volatility. A 2.5% OTM strike on a low-volatility stock (KO, daily ATR ~1.0%) is deep OTM (~0.25 Delta), needing a multi-day outlier move. On a high-beta stock (NVDA, daily ATR ~4.0%), 2.5% OTM is virtually ATM (~0.48 Delta).
Target Delta or ATR-Scaled Strikes: Pick the strike closest to a target Delta (0.35–0.45 Δ) or scale OTM distance by ATR (e.g., 0.4 × ATR_14 for 12–20 DTE, 0.8 × ATR_14 for 30–45 DTE). This equalizes the probability of profit across tickers.
Fixed 1-Contract Sizing (AGENT_OPTION_ORDER_QTY = 1)
Creates extreme dollar-risk skew. One contract on a $500 stock might cost $1,600 (a -35% stop loses $560), while one contract on a $35 stock costs $120 (a -35% stop loses $42). A single loss on a high-priced ticker can wipe out 10 winners on cheaper tickers.
Fixed-Dollar Risk Sizing: Define a constant dollar risk per trade (e.g., R=$200 max loss at the stop level):
Contracts=⌊Option Premium×∣Stop Loss %∣R​⌋
If 1 contract exceeds 1.5×R, skip the trade or switch to a vertical debit spread.
1-Contract Default vs. Partial Trailing Sells
Rule conflict. Section 4 defaults to 1 contract, while Section 5 states "part of the position is sold at set triggers." You cannot scale out of a 1-contract position—either the partial-exit logic never fires, or rounding sells 100% of the position prematurely at +20%.
Dynamic Exit Mode by Lot Size: In _option_positions_strategy.py, branch logic explicitly: if qty == 1, disable partial sells and rely purely on the ratcheting trailing floor; only enable partial scale-outs when qty >= 2.

2. News Pipeline & Catalyst Hygiene (news_collector)
Top-down filtering (Macro → Sector → Industry → Company) is great for catching sector rotation, but it creates two structural blind spots for news-catalyst momentum:
Add a "Bottom-Up Idiosyncratic Bypass": If a single company has a major standalone catalyst (FDA approval, activist stake, surprise contract win, guidance raise) during a quiet week for its broader sector, top-down sector ranking may filter it out before Strategist ever sees it. Add a parallel fast-lane that injects any stock with abnormal news velocity + RVOL greater than 2.0 directly into the candidate pool.
Track "Time Since First Print" & Cumulative Price Drift: Even with PIPELINE_RANKING_MAX_AGE_DAYS, financial media syndicates the same catalyst across dozens of outlets over 48 hours. LLMs frequently read a fresh syndication of 2-day-old news and treat it as a brand-new catalyst. Have Python deduplicate articles into Event Clusters, log the first_seen_timestamp, and compute the stock's % move since first_seen_timestamp. Pass that exact number to Strategist and Manager so they know how much of the move has already happened.
Hard Binary-Event (Earnings/FOMC) Gate: Buying single-leg OTM options 1–4 days before an earnings report based on pre-earnings news buzz exposes the trade to severe IV crush the morning after earnings, even if the direction is right. Add a deterministic check in Section 3 that blocks new entries if days_to_earnings <= 5 (or forces an exit prior to the print).
3. Strategist & Manager Agent Design
Your two-stage setup (Strategist on Flash for fundamental thesis, Manager on Pro for technical/volatility filtering) is well-structured, but two subtle LLM behaviors often degrade multi-agent consistency:
Prevent Sycophancy / Anchoring Between Agents: If Manager receives Strategist's persuasive prose thesis, LLMs have a documented tendency to rationalize borderline technicals (e.g., forgiving a slightly elevated RSI or weak VWAP alignment) to agree with a compelling narrative. Strip the narrative prose before calling Manager—pass only a structured JSON summary (direction, catalyst_category, horizon, citation_count) alongside the raw technical data.
Replace Free-Form Confidence Scores with a Deterministic Rubric: LLMs are poorly calibrated when outputting raw confidence floats (often clustering at 0.75, 0.80, or 0.85). Instead, force Strategist to answer 5–6 binary (true/false) rubric questions in structured JSON (e.g., Does the catalyst include quantified revenue/EPS impact? Is the source a primary filing or tier-1 outlet? Are there zero conflicting bearish articles?), and let Python calculate the confidence score deterministically.
Feed Implied Move vs. ATR Directly Into the Option Hurdle: Make sure your Option Hurdle in Section 3 compares the option's breakeven distance (Strike+Premium−Spot) plus expected Theta decay over the expected holding period against the stock's historical post-catalyst ATR expansion—not just the LLM's subjective "expected move" estimate.
4. Entry Gates & Portfolio Risk Controls
Promote the Market-Regime Gate Out of Shadow Mode: Roughly 60–75% of an individual stock's short-term trajectory is driven by broad market (SPY/QQQ) and sector beta. Running the Market-Regime gate in shadow mode means a burst of bullish company news can trigger long calls right into a macro market sell-off where correlations spike to 1. Activate it as a tiered filter: if the trade direction opposes the broad market's intraday VWAP and 20-DMA trend, block macro and sector catalysts outright, and require company-specific catalysts to pass a 1.5x higher Option Hurdle.
Refine the "No Friday Entries" Rule by DTE Bucket: Blocking Friday entries makes complete sense for the 12–20 DTE bucket where weekend theta decay is steep. However, entering a 12–20 DTE trade on Thursday at 3:00 PM still carries almost the exact same weekend risk. Conversely, blocking a 45–60 DTE trade on Friday morning when a massive catalyst breaks forfeits first-mover momentum on a contract with negligible weekend theta. Tie the day-of-week cutoff to the DTE bucket and expected holding period rather than a blanket Friday ban.
Portfolio-Level Net Delta Cap: Your cluster limit (1 position / max 25% per cluster) prevents sector concentration, but you can still end up with 5 long calls across 5 different sectors—making the portfolio 100% net long market beta. Add a simple portfolio gate: cap total same-direction positions at 3 or 4 out of 5, or cap net portfolio beta-weighted Delta.
5. Exit Execution & Paper-Trading Realism
Replace the 10-Minute Exit Cancel with an Escalating Limit Ladder: In _option_positions_strategy.py, cancelling a stale pending exit after 10 minutes is dangerous during a fast drawdown. If a stock breaks down and your stop-loss limit order sits at the initial mid-price, the market will drop away from your limit order, and cancelling it after 10 minutes leaves the position unhedged or re-submitting at much worse prices. Instead, implement a Walk-the-Book Ladder:
Minute 0: Submit limit exit at Mid.
Minute 2: Modify limit price to Mid - 25% of Bid/Ask Spread.
Minute 4: Modify to Mid - 50% of Spread.
Minute 5 (for Stop-Loss / Forced Exits): Cross the spread to the Bid to guarantee liquidation.
Account for Alpaca Paper Fill Optimism: Alpaca's paper trading engine fills marketable limit orders against the NBBO without modeling market-maker spread widening, queue priority, or adverse selection. Even with your 6% max spread filter, getting filled at the mid-price on both entry and exit in paper trading gives your strategy a synthetic 3% to 6% round-trip cushion that won't exist live. In your Decision Autopsy ledger, log a Slippage-Adjusted PnL that deducts 35% of the bid-ask spread on every entry and exit so you know which variants survive realistic execution friction.
6. Research Layer & Calibration Feedback Loop
Avoid Prompt Drift—Calibrate in Python, Not Prompt Text: Feeding backtest and Decision Autopsy results back into Strategist and Manager prompts as natural-language calibration rules can introduce recency bias and prompt bloat (e.g., the model over-indexing on last month's losing trades and missing the next regime shift). Keep the LLM prompts stationary (focused strictly on extracting consistent catalyst and technical features) and apply your Decision Autopsy findings as deterministic Bayesian weights or threshold adjustments in Python (for example, dynamically raising the required technical score for 12–20 DTE trades when recent short-horizon hit rates drop).
Interactive Architecture & Upgrade Explorer
Click through the six stages of your trading pipeline below to compare your current implementation against the highest-leverage upgrades and see how each change improves consistency:

User prompt: Help me redesign the Strategist and Manager JSON schemas and prompts so Strategist uses a deterministic binary rubric instead of raw confidence, and Manager evaluates technicals without anchoring on Strategist's prose.
Response: To eliminate uncalibrated confidence floats and prevent Manager from being swayed by Strategist's narrative prose, you can introduce a three-part "Information Airlock" architecture:
Autoregressive Schema Ordering (Strategist): Because LLMs generate JSON tokens sequentially from top to bottom, putting decision or confidence at the top of a schema causes the model to pick a side first and rationalize the evidence afterward. By placing cited_article_ids and risks_and_contradictions before the binary rubric—and decision last—you force Gemini Flash to confront contrary evidence before answering the true/false rubric questions.
Deterministic Python Airlock (airlock_middleware.py): Python validates that every cited article ID actually exists in the scraped pool, computes a weighted confidence score from the binary rubric, saves the prose thesis to your Decision Autopsy ledger, and strips all narrative text before constructing the payload for Manager.
Adversarial Checklist Gate (Manager): Gemini Pro receives only categorical catalyst metadata (direction, catalyst_category, rubric_score, hours_since_first_print) alongside raw technicals (VWAP, RSI, ATR move, IV percentile, RVOL). It must complete its own binary technical checklist before outputting call, put, or neither.
1. Strategist Schema & Prompt (strategist_prompt.py)
This Pydantic schema is designed to pass directly into Gemini Flash's response_schema configuration. Notice the field order: citations and counter-arguments come first, followed by the 6 binary rubric questions, and finally the categorical outputs.
from enum import Enum
from typing import List
from pydantic import BaseModel, Field

class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"

class HorizonBucket(str, Enum):
    SHORT_12_20_DTE = "12_20"
    MEDIUM_20_30_DTE = "20_30"
    STANDARD_30_45_DTE = "30_45"
    LONG_45_60_DTE = "45_60"

class CatalystCategory(str, Enum):
    EARNINGS_OR_GUIDANCE = "earnings_or_guidance"
    REGULATORY_OR_CLINICAL = "regulatory_or_clinical"
    CONTRACT_OR_PARTNERSHIP = "contract_or_partnership"
    MA_OR_ACTIVIST = "ma_or_activist"
    ANALYST_OR_SENTIMENT = "analyst_or_sentiment"
    MACRO_OR_SECTOR_SPILLOVER = "macro_or_sector_spillover"

class StrategistBinaryRubric(BaseModel):
    is_primary_or_tier1_source: bool = Field(
        description="True ONLY if the core catalyst originates from an official filing, company release, regulatory agency, or tier-1 wire (Reuters, Bloomberg, WSJ, CNBC)—False if only blogs, opinion pieces, or unverified chatter."
    )
    has_quantified_financial_impact: bool = Field(
        description="True ONLY if the cited articles contain concrete numbers (e.g., revenue/EPS guidance %, contract dollar value, margin shift, price target delta) rather than vague qualitative commentary."
    )
    is_single_company_direct_driver: bool = Field(
        description="True if the catalyst directly changes THIS specific company's fundamentals; False if the stock is merely mentioned as a sympathy play on macro or sector news."
    )
    is_unanimously_directional: bool = Field(
        description="True ONLY if the cited articles point in a single clear direction without major offsetting bad news (e.g., a revenue beat paired with a guidance cut is False)."
    )
    is_multi_day_structural_catalyst: bool = Field(
        description="True if the fundamental shift is large enough to drive institutional repositioning over 3 to 15 trading days; False if it is a fleeting 1-day headline."
    )
    survives_contradiction_stress_test: bool = Field(
        description="True ONLY if the risks_and_contradictions listed above do NOT invalidate or overshadow the primary directional thesis."
    )

class StrategistOutput(BaseModel):
    # STEP 1: Grounding & Adversarial Audit (Generated FIRST to prevent post-hoc rationalization)
    cited_article_ids: List[str] = Field(
        description="Exact IDs of stored articles from the input prompt that directly support the thesis."
    )
    risks_and_contradictions: List[str] = Field(
        description="Specific counter-arguments, conflicting data points in the articles, or reasons this trade could fail."
    )
    thesis_for_autopsy_log: str = Field(
        description="Concise 2-3 sentence fundamental thesis citing specific facts. Used ONLY for post-trade audit logs."
    )
    
    # STEP 2: Deterministic Binary Rubric
    rubric: StrategistBinaryRubric
    
    # STEP 3: Categorical Classification (Generated LAST)
    catalyst_category: CatalystCategory
    expected_direction: Direction
    recommended_horizon: HorizonBucket
    decision: str = Field(description="Must be 'buy' or 'do_not_buy'.")


STRATEGIST_SYSTEM_PROMPT
STRATEGIST_SYSTEM_PROMPT = """
You are the Fundamental Catalyst Strategist for a systematic US equity options desk.
Your job is to evaluate scraped news articles for a single stock and complete a strict factual audit.

CRITICAL OPERATING RULES:
1. GROUNDING ONLY: You may only cite article IDs explicitly provided in the user prompt. Never invent or extrapolate facts not present in the text.
2. ADVERSARIAL ORDERING: You must populate `risks_and_contradictions` BEFORE answering the boolean `rubric`. Actively look for reasons the news is stale, already priced in, syndicated re-reporting, or contradicted by other details in the text.
3. STRICT BOOLEAN RUBRIC: Treat every field in `rubric` as a strict pass/fail audit. When evidence is ambiguous or missing, you MUST set the boolean to `false`.
4. DO NOT SCORE CONFIDENCE: Do not estimate numerical probabilities. Your boolean rubric answers will be scored deterministically by downstream code.
5. SYNDICATION TRAP WARNING: Check the `hours_since_first_print` and `price_drift_since_first_print_pct` provided in the prompt metadata. If multiple articles repeat the same event that broke over 24 hours ago and the stock has already moved significantly, set `survives_contradiction_stress_test` to `false`.
"""


2. The Python "Information Airlock" (airlock_middleware.py)
This deterministic middleware sits between Strategist and Manager. It verifies citations, converts the 6 booleans into a calibrated score (0.00 to 1.00), logs the full prose output to your Decision Autopsy ledger, and outputs a prose-free structure for Manager.
from dataclasses import dataclass
from typing import Dict, Set, Optional

# Deterministic weights summing to 1.00
RUBRIC_WEIGHTS: Dict[str, float] = {
    "is_primary_or_tier1_source": 0.20,
    "has_quantified_financial_impact": 0.20,
    "is_single_company_direct_driver": 0.15,
    "is_unanimously_directional": 0.15,
    "is_multi_day_structural_catalyst": 0.15,
    "survives_contradiction_stress_test": 0.15,
}

MIN_PASSING_SCORE = 0.70

@dataclass(frozen=True)
class SanitizedCatalystSignal:
    """Strictly numerical and categorical metadata passed to Manager (ZERO prose)."""
    symbol: str
    proposed_direction: str          # "bullish" or "bearish"
    catalyst_category: str           # Enum string
    recommended_horizon: str         # "12_20", "20_30", "30_45", "45_60"
    deterministic_rubric_score: float
    verified_citation_count: int
    hours_since_first_print: float
    price_drift_since_catalyst_pct: float

def evaluate_and_sanitize_strategist(
    symbol: str,
    strategist_out: StrategistOutput,
    valid_article_ids: Set[str],
    hours_since_first_print: float,
    price_drift_since_catalyst_pct: float,
) -> Optional[SanitizedCatalystSignal]:
    """
    Validates Strategist output, computes deterministic rubric score,
    and strips all narrative prose so Manager cannot anchor on the story.
    """
    # 1. Respect Strategist's own veto or neutral direction
    if strategist_out.decision != "buy" or strategist_out.expected_direction == Direction.NEUTRAL:
        return None

    # 2. Citation hallucination check: keep only IDs that actually exist in scraped pool
    verified_ids = [aid for aid in strategist_out.cited_article_ids if aid in valid_article_ids]
    if not verified_ids:
        return None  # Hard veto: ungrounded thesis

    # 3. Hard Veto gates on essential rubric booleans
    rubric_dict = strategist_out.rubric.model_dump()
    if not rubric_dict["survives_contradiction_stress_test"]:
        return None
    if not (rubric_dict["is_primary_or_tier1_source"] or rubric_dict["has_quantified_financial_impact"]):
        return None  # Reject pure low-tier unquantified chatter

    # 4. Compute deterministic weighted rubric score
    score = round(
        sum( weight for key, weight in RUBRIC_WEIGHTS.items() if rubric_dict.get(key, False) ),
        4
    )
    if score < MIN_PASSING_SCORE:
        return None

    # 5. Return strictly sanitized, prose-free signal for Manager
    return SanitizedCatalystSignal(
        symbol=symbol,
        proposed_direction=strategist_out.expected_direction.value,
        catalyst_category=strategist_out.catalyst_category.value,
        recommended_horizon=strategist_out.recommended_horizon.value,
        deterministic_rubric_score=score,
        verified_citation_count=len(verified_ids),
        hours_since_first_print=round(hours_since_first_print, 2),
        price_drift_since_catalyst_pct=round(price_drift_since_catalyst_pct, 2),
    )


3. Manager Schema & Prompt (manager_prompt.py)
Because Manager never sees thesis_for_autopsy_log or the news headlines, it cannot fall in love with a narrative and excuse bad technicals. Furthermore, we apply the same checklist-first schema design to Manager, and then verify its checklist in Python so the LLM cannot contradict its own technical findings.
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

class ManagerAction(str, Enum):
    CALL = "call"
    PUT = "put"
    NEITHER = "neither"

class TechnicalRiskChecklist(BaseModel):
    vwap_and_trend_aligned: bool = Field(
        description="True ONLY if price and intraday VWAP/daily trend agree with `proposed_direction`."
    )
    volume_and_rs_confirmed: bool = Field(
        description="True ONLY if relative volume (RVOL >= 1.2) and relative strength vs SPY/sector confirm institutional participation in `proposed_direction`."
    )
    not_overextended_rsi_or_atr: bool = Field(
        description="True ONLY if RSI is not extreme (<72 for calls, >28 for puts) AND intraday move / `price_drift_since_catalyst_pct` is <= 1.5x daily ATR."
    )
    iv_is_acceptable_for_long_option: bool = Field(
        description="True ONLY if IV Percentile is <= 70% (or <= 80% when `deterministic_rubric_score` == 1.0)."
    )
    evidence_is_fresh: bool = Field(
        description="True ONLY if `hours_since_first_print` fits the DTE horizon and price has not already stalled after the initial impulse."
    )

class ManagerOutput(BaseModel):
    # STEP 1: Evaluate independent technical & volatility gates FIRST
    technical_checklist: TechnicalRiskChecklist
    
    # STEP 2: Concise technical audit note for Decision Autopsy
    technical_audit_note: str = Field(
        description="1-2 sentence explanation citing specific technical numbers (RSI, ATR multiple, IV%, VWAP) that drove the decision."
    )
    
    # STEP 3: Final Action & Horizon
    selected_dte_bucket: Optional[HorizonBucket] = Field(
        description="Select '12_20', '20_30', '30_45', or '45_60' if trading; null if action is 'neither'."
    )
    action: ManagerAction


MANAGER_SYSTEM_PROMPT & Post-Check Enforcement
MANAGER_SYSTEM_PROMPT = """
You are the Risk & Execution Manager for a systematic US equity options desk.
You do NOT generate trade ideas and you are intentionally blinded to the news story prose to prevent narrative bias.

Your sole role is to act as an adversarial technical and volatility filter on a candidate signal.

INPUT DATA YOU WILL RECEIVE:
1. `sanitized_signal`: Contains `proposed_direction` (bullish/bearish), `catalyst_category`, `deterministic_rubric_score` (0.70 to 1.00), `hours_since_first_print`, and `price_drift_since_catalyst_pct`.
2. `market_technicals`: Objective quantitative metrics (Price vs VWAP, 20/50 DMA alignment, RVOL, Relative Strength vs Sector/SPY, 14-day RSI, Move in ATR units, IV Percentile).

DECISION RULES:
- DEFAULT TO `neither`: You are paid to block bad entries. If technicals are mixed, volume is weak, or the move has already happened, output `neither`.
- DIRECTION LOCK: You may NEVER flip a `bullish` signal into a `put` or a `bearish` signal into a `call`. You may only confirm the proposed direction (`call` for bullish, `put` for bearish) or reject it (`neither`).
- OVEREXTENSION PENALTY: If the stock has already moved more than 1.5x its daily ATR since the catalyst broke, or RSI is overextended in the direction of the trade, set `not_overextended_rsi_or_atr` to `false` and choose `neither`. Buying OTM options after a 1.5x+ ATR expansion has negative expectancy due to mean-reversion and IV crush.
- IV PENALTY: Long single-leg OTM options require reasonable implied volatility. Reject trades where IV Percentile exceeds 70% unless `deterministic_rubric_score` is 1.00 and IV Percentile is at most 80%.
- DTE BUCKET SELECTION: Start from `recommended_horizon`, but shift one bucket longer (e.g., `20_30` -> `30_45`) if IV Percentile is above 55% or if `catalyst_category` is `macro_or_sector_spillover` to reduce Theta bleed.
"""

def enforce_manager_consistency(
    signal: SanitizedCatalystSignal,
    manager_out: ManagerOutput
) -> ManagerOutput:
    """
    Deterministic post-check ensuring Manager cannot output 'call' or 'put'
    if it failed any of its own technical checklist gates or flipped direction.
    """
    checks = manager_out.technical_checklist.model_dump()
    
    # 1. If ANY technical checklist item is False, force 'neither'
    if not all(checks.values()):
        manager_out.action = ManagerAction.NEITHER
        manager_out.selected_dte_bucket = None
        return manager_out

    # 2. Enforce strict directional agreement with Strategist
    if signal.proposed_direction == "bullish" and manager_out.action != ManagerAction.CALL:
        manager_out.action = ManagerAction.NEITHER
        manager_out.selected_dte_bucket = None
    elif signal.proposed_direction == "bearish" and manager_out.action != ManagerAction.PUT:
        manager_out.action = ManagerAction.NEITHER
        manager_out.selected_dte_bucket = None

    return manager_out


Why This Upgrade Immediately Improves Your Decision Autopsy Ledger
With this structure in place, your Decision Autopsy in research_contract.py gains clean, orthogonal features to analyze in Python:
Instead of trying to figure out why an 0.80 confidence trade lost money, you can run a simple group-by query on the 6 Strategist rubric booleans and 5 Manager technical booleans to see which specific boolean correlates with losing trades.
If you discover that ANALYST_OR_SENTIMENT catalysts only win when RUBRIC_SCORE >= 0.85, you can change MIN_PASSING_SCORE in airlock_middleware.py in two seconds—without rewriting or destabilizing your LLM prompts.

User prompt: Show me how to deduplicate syndicated news articles into Event Clusters in Python so I can pass accurate hours_since_first_print and price_drift_since_catalyst_pct to Strategist and Manager.
Response: To stop syndicated re-reporting from fooling Strategist and Manager into chasing stale moves, your deduplication layer has to solve three specific traps common in financial news feeds:
The Syndicated Rewrite Trap (Transitive Chaining): At 8:00 AM Tuesday, Reuters prints "XYZ Wins $450M DoD Cloud Contract." At 1:30 PM, Benzinga prints "Why XYZ Stock Is Surging 9% Today." At 10:00 AM Wednesday, Motley Fool prints "Is It Too Late to Buy XYZ After Its Government Deal?" Article 1 and Article 3 share very few words, but Article 2 bridges them. Using single-linkage clustering combined with fact-token extraction (matching numbers, dollar figures, and named entities) chains 1→2→3 into a single event anchored to Tuesday at 8:00 AM.
The Press-Release Boilerplate Trap: Comparing full article bodies causes false merges because every press release for a company ends with the exact same 200-word "About [Company]" and "Forward-Looking Statements" paragraphs. You should only compare the headline + first 400 characters (the lead).
The Overnight Gap Anchor Trap: If a catalyst drops at 4:15 PM Tuesday (after the bell) or 7:30 AM Wednesday (pre-market) and the stock gaps up +12% at Wednesday's 9:30 AM open, looking up the price at or after Wednesday's open makes price_drift_since_catalyst_pct look like 0.3% instead of +12.3%. Your baseline price (Panchor​) must always be the last regular-session bar strictly at or before first_seen_timestamp.
1. Event Clustering & Drift Engine (event_cluster_engine.py)
This self-contained module uses zero heavy ML dependencies (pure Python standard library for tokenization, TF-IDF cosine similarity, and numeric/entity fingerprinting, plus zoneinfo for New York market-session math) so it runs in milliseconds inside news_collector.
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Callable, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

# Standard financial stopwords that add noise to similarity checks
FINANCIAL_STOPWORDS: Set[str] = {
    "the", "and", "for", "that", "this", "with", "from", "its", "are", "was",
    "has", "have", "had", "will", "would", "could", "should", "inc", "corp",
    "ltd", "plc", "shares", "stock", "stocks", "company", "market", "markets",
    "trading", "today", "monday", "tuesday", "wednesday", "thursday", "friday",
    "report", "reports", "reported", "news", "update", "why", "what", "how",
    "NYSE", "NASDAQ", "moving", "higher", "lower", "gains", "falls", "jumps",
}

SOURCE_TIER_RANK: Dict[str, int] = {
    "sec_filing": 1, "pr_newswire": 1, "business_wire": 1, "globenewswire": 1,
    "reuters": 1, "bloomberg": 1, "wsj": 1, "cnbc": 1, "dow_jones": 1,
    "benzinga": 2, "marketwatch": 2, "barrons": 2, "the_fly": 2,
    "yahoo_finance": 3, "seeking_alpha": 3, "motley_fool": 3, "zacks": 3,
}


@dataclass(frozen=True)
class RawArticle:
    article_id: str
    symbol: str
    headline: str
    summary_or_body: str
    source: str
    published_at: datetime  # Must be timezone-aware (UTC or America/New_York)


@dataclass
class EventCluster:
    cluster_id: str
    symbol: str
    articles: List[RawArticle] = field(default_factory=list)
    first_seen_timestamp: Optional[datetime] = None
    latest_timestamp: Optional[datetime] = None
    primary_article: Optional[RawArticle] = None
    # Computed market timing & price drift metrics
    hours_since_first_print: float = 0.0
    market_hours_since_first_print: float = 0.0
    anchor_price_at_first_print: Optional[float] = None
    current_price: Optional[float] = None
    price_drift_since_catalyst_pct: float = 0.0
     price_drift_in_atr_units: float = 0.0

    @property
    def article_ids(self) -> List[str]:
        return [a.article_id for a in self.articles]

    @property
    def unique_sources_count(self) -> int:
        return len({a.source.lower() for a in self.articles})


# ---------------------------------------------------------------------------
# 1. Text Normalization & Fact-Token Fingerprinting
# ---------------------------------------------------------------------------

def _extract_lead_text(article: RawArticle, max_body_chars: int = 400) -> str:
    """
    Takes only the headline + first 400 chars of body to avoid matching
    company boilerplate ('About XYZ Corp') at the bottom of press releases.
    """
    lead = (article.summary_or_body or "")[:max_body_chars]
    return f"{article.headline} {lead}"


def _tokenize_words(text: str, symbol: str) -> List[str]:
    """Extracts normalized content tokens, stripping the ticker itself and stopwords."""
    sym_lower = symbol.lower()
    raw_tokens = re.findall(r"[a-zA-Z0-9$%.\-]+", text.lower())
    cleaned = []
    for tok in raw_tokens:
        tok_stripped = tok.strip(".-")
        if len(tok_stripped) <= 2 or tok_stripped == sym_lower:
            continue
        if tok_stripped in FINANCIAL_STOPWORDS:
            continue
        cleaned.append(tok_stripped)
    return cleaned


def _extract_fact_anchors(text: str, symbol: str) -> Set[str]:
    """
    Extracts hard quantitative/entity anchors: dollar figures ($450M, $2.10),
    percentages (14%, 25%), quarters/years (Q3, FY26), and regulatory/agency acronyms.
    Two articles citing the same dollar amount or percentage on the same ticker
    within 48h are almost always covering the same underlying event.
    """
    anchors: Set[str] = set()
    # 1. Dollar figures and percentages (e.g., $45, $1.2b, 18%)
    for match in re.findall(r"\$\d+(?:\.\d+)?[mbk]?|\d+(?:\.\d+)?%", text.lower()):
        anchors.add(match)
    # 2. Key uppercase acronyms/entities (FDA, DOJ, FTC, SEC, CEO, CFO, DoD, NDA, PDUFA)
    for match in re.findall(r"\b[A-Z]{3,6}\b", text):
        if match.upper() != symbol.upper() and match.lower() not in FINANCIAL_STOPWORDS:
            anchors.add(match.lower())
    return anchors


def _compute_idf(articles: List[RawArticle]) -> Dict[str, float]:
    """Computes inverse document frequency across the candidate article pool."""
    n_docs = len(articles)
    doc_freq: Counter = Counter()
    for art in articles:
        unique_tokens = set(_tokenize_words(_extract_lead_text(art), art.symbol))
        for tok in unique_tokens:
            doc_freq[tok] += 1
    # Smooth IDF so small pools don't zero out shared event terms
    return {
        tok: math.log((1.0 + n_docs) / (1.0 + df)) + 1.0
        for tok, df in doc_freq.items()
    }


def _pair_similarity(
    art_a: RawArticle,
    art_b: RawArticle,
    idf: Dict[str, float],
    max_cluster_span_hours: float = 72.0,
) -> float:
    """
    Computes composite event similarity [0.0, 1.0] between two articles on the same ticker.
    Combines TF-IDF cosine similarity + Fact-Anchor overlap, gated by time proximity.
    """
    if art_a.symbol != art_b.symbol:
        return 0.0

    # Time separation check
    dt_hours = abs((art_a.published_at - art_b.published_at).total_seconds()) / 3600.0
    if dt_hours > max_cluster_span_hours:
        return 0.0

    text_a = _extract_lead_text(art_a)
    text_b = _extract_lead_text(art_b)

    # 1. TF-IDF Weighted Cosine Similarity
    tokens_a = Counter(_tokenize_words(text_a, art_a.symbol))
    tokens_b = Counter(_tokenize_words(text_b, art_b.symbol))
    if not tokens_a or not tokens_b:
        return 0.0

    vec_a = {t: count * idf.get(t, 1.0) for t, count in tokens_a.items()}
    vec_b = {t: count * idf.get(t, 1.0) for t, count in tokens_b.items()}

    shared_keys = set(vec_a.keys()) & set(vec_b.keys())
    dot = sum(vec_a[k] * vec_b[k] for k in shared_keys)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    cosine_sim = dot / (norm_a * norm_b) if (norm_a and norm_b) else 0.0

    # 2. Hard Fact Anchor Overlap (Numbers, $, %, Acronyms)
    anchors_a = _extract_fact_anchors(text_a, art_a.symbol)
    anchors_b = _extract_fact_anchors(text_b, art_b.symbol)
    shared_anchors = anchors_a & anchors_b

    anchor_boost = 0.0
    if shared_anchors:
        union_size = len(anchors_a | anchors_b)
        jaccard = len(shared_anchors) / union_size if union_size else 0.0
        # Sharing a specific dollar figure or percentage on the same ticker is a strong signal
        anchor_boost = min(0.25, 0.12 * len(shared_anchors) + 0.15 * jaccard)

    # Slight decay for articles published more than 24h apart
    time_decay = 1.0 if dt_hours <= 24.0 else max(0.80, 1.0 - (dt_hours - 24.0) * 0.004)

    return min(1.0, (cosine_sim + anchor_boost) * time_decay)


# ---------------------------------------------------------------------------
# 2. Single-Linkage Event Clustering & Market-Hours Math
# ---------------------------------------------------------------------------

def compute_regular_market_hours_elapsed(start_dt: datetime, end_dt: datetime) -> float:
    """
    Calculates how many regular US equity trading hours (09:30-16:00 ET, Mon-Fri)
    have elapsed between `start_dt` and `end_dt`.
    Prevents Friday 16:05 ET news from appearing '65 hours stale' at Monday 09:35 ET.
    """
    s_ny = start_dt.astimezone(NY_TZ)
    e_ny = end_dt.astimezone(NY_TZ)
    if e_ny <= s_ny:
        return 0.0

    total_seconds = 0.0
    cur_date = s_ny.date()
    end_date = e_ny.date()

    while cur_date <= end_date:
        if cur_date.weekday() < 5:  # Monday=0 .. Friday=4
            sess_open = datetime.combine(cur_date, MARKET_OPEN, tzinfo=NY_TZ)
            sess_close = datetime.combine(cur_date, MARKET_CLOSE, tzinfo=NY_TZ)
            interval_start = max(s_ny, sess_open)
            interval_end = min(e_ny, sess_close)
            if interval_end > interval_start:
                total_seconds += (interval_end - interval_start).total_seconds()
        cur_date += timedelta(days=1)

    return round(total_seconds / 3600.0, 2)


def cluster_company_news(
    articles: List[RawArticle],
    as_of_dt: datetime,
    price_lookup_fn: Callable[[str, datetime], Optional[float]],
    current_price: float,
    daily_atr: float,
    similarity_threshold: float = 0.34,
) -> List[EventCluster]:
    """
    Groups a single company's scraped articles into deduplicated EventClusters,
    identifies the true `first_seen_timestamp` for each event, and computes
    exact price drift since the pre-catalyst anchor price.

    Parameters
    ----------
    articles : List[RawArticle]
        All scraped articles for this symbol within PIPELINE_RANKING_MAX_AGE_DAYS.
    as_of_dt : datetime
        Current pipeline evaluation timestamp (timezone-aware).
    price_lookup_fn : Callable[[str, datetime], Optional[float]]
        Function returning the last regular-session trade/bar close AT OR BEFORE
        the given timestamp.
    current_price : float
        Latest underlying spot price.
    daily_atr : float
        Current 14-day ATR in dollars (used to normalize drift in ATR units).
    similarity_threshold : float
        Threshold [0.0, 1.0] for linking two articles into the same event cluster.
    """
    if not articles:
        return []

    # Sort chronologically (oldest first) so cluster roots are naturally the first print
    sorted_arts = sorted(articles, key=lambda a: a.published_at)
    n = len(sorted_arts)
    idf = _compute_idf(sorted_arts)

    # Disjoint-Set (Union-Find) for Single-Linkage Transitive Clustering (A -> B -> C)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        root_i, root_j = find(i), find(j)
        if root_i != root_j:
            # Always keep the older article index as root
            if root_i < root_j:
                parent[root_j] = root_i
            else:
                parent[root_i] = root_j

    for i in range(n):
        for j in range(i + 1, n):
            if _pair_similarity(sorted_arts[i], sorted_arts[j], idf) >= similarity_threshold:
                union(i, j)

    # Group articles by their root index
    grouped: Dict[int, List[RawArticle]] = {}
    for idx, art in enumerate(sorted_arts):
        root = find(idx)
        grouped.setdefault(root, []).append(art)

    clusters: List[EventCluster] = []
    for root_idx, cluster_arts in grouped.items():
        first_art = cluster_arts[0]  # Guaranteed oldest because sorted_arts is chronological
        last_art = cluster_arts[-1]

        # Pick the highest-authority article (Tier 1 wire/filing, or most detailed) as primary
        primary_art = min(
            cluster_arts,
            key=lambda a: (
                SOURCE_TIER_RANK.get(a.source.lower(), 4),
                -len(a.summary_or_body or ""),
            ),
        )

        first_ts = first_art.published_at
        wall_hours = round(max(0.0, (as_of_dt - first_ts).total_seconds() / 3600.0), 2)
        mkt_hours = compute_regular_market_hours_elapsed(first_ts, as_of_dt)

        # Look up the pre-catalyst anchor price (strictly at or before first_ts)
        anchor_price = price_lookup_fn(first_art.symbol, first_ts)
        drift_pct = 0.0
        drift_atr = 0.0
        if anchor_price and anchor_price > 0 and current_price > 0:
            drift_pct = round(((current_price - anchor_price) / anchor_price) * 100.0, 2)
            if daily_atr > 0:
                drift_atr = round(abs(current_price - anchor_price) / daily_atr, 2)

        clusters.append(
            EventCluster(
                cluster_id=f"{first_art.symbol}_EVT_{first_ts.strftime('%Y%m%d_%H%M')}_{root_idx}",
                symbol=first_art.symbol,
                articles=cluster_arts,
                first_seen_timestamp=first_ts,
                latest_timestamp=last_art.published_at,
                primary_article=primary_art,
                hours_since_first_print=wall_hours,
                market_hours_since_first_print=mkt_hours,
                anchor_price_at_first_print=anchor_price,
                current_price=current_price,
                price_drift_since_catalyst_pct=drift_pct,
                price_drift_in_atr_units=drift_atr,
            )
        )

    # Sort clusters so the highest-authority / most-corroborated recent cluster is first
    clusters.sort(
        key=lambda c: (c.unique_sources_count, c.latest_timestamp),
        reverse=True,
    )
    return clusters


2. Alpaca Pre-Catalyst Price Anchor Lookup (alpaca_price_anchor.py)
This helper implements the price_lookup_fn required above. It explicitly filters for regular market hours (09:30–16:00 ET) and grabs the last bar at or before first_seen_timestamp.
If an earnings release or FDA announcement drops at 4:10 PM Tuesday or 8:00 AM Wednesday, this function returns Tuesday's 4:00 PM regular session close, ensuring that the entire overnight/pre-market gap is included in price_drift_since_catalyst_pct.
from datetime import datetime, timedelta
from typing import Optional
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")

def get_pre_catalyst_regular_session_price(
    data_client: StockHistoricalDataClient,
    symbol: str,
    first_seen_timestamp: datetime,
) -> Optional[float]:
    """
    Returns the last regular-session (09:30-16:00 ET) 5-minute bar close
    strictly AT OR BEFORE `first_seen_timestamp`.
    """
    target_ny = first_seen_timestamp.astimezone(NY_TZ)
    # Look back 5 days to safely span weekends and 3-day market holidays
    start_lookback = target_ny - timedelta(days=5)

    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(5, TimeFrameUnit.Minute),
        start=start_lookback,
        end=target_ny,
        adjustment="all",
    )
    bars_resp = data_client.get_stock_bars(req)
    symbol_bars = bars_resp.data.get(symbol, [])
    if not symbol_bars:
        return None

    # Filter strictly to regular trading session bars (09:30 <= bar_time <= 16:00 ET)
    regular_bars = []
    for bar in symbol_bars:
        bar_ny = bar.timestamp.astimezone(NY_TZ)
        if bar_ny.weekday() < 5 and (9, 30) <= (bar_ny.hour, bar_ny.minute) <= (16, 0):
            if bar_ny <= target_ny:
                regular_bars.append(bar)

    if not regular_bars:
        return float(symbol_bars[-1].close)

    # The final bar in this list is the exact regular-session price right before the news broke
    return float(regular_bars[-1].close)


3. Formatting the Clustered Payload for Strategist & Airlock
Instead of dumping 15 repetitive syndicated articles into Strategist's context window (which wastes tokens and makes the LLM think 15 separate bullish things happened), format the prompt by Event Cluster and pass the primary cluster's timing and drift metrics directly into evaluate_and_sanitize_strategist():
def build_strategist_cluster_prompt(
    symbol: str,
    clusters: List[EventCluster],
    max_articles_per_cluster: int = 3,
) -> str:
    """
    Formats deduplicated EventClusters for Strategist so it sees exact
    syndication counts, first-print age, and cumulative price drift.
    """
    lines = [f"CANDIDATE TICKER: {symbol}", f"TOTAL DISTINCT EVENT CLUSTERS: {len(clusters)}\n"]

    for idx, cluster in enumerate(clusters, start=1):
        lines.append(f"=== EVENT CLUSTER #{idx} ({cluster.cluster_id}) ===")
        lines.append(
            f"- First Print Timestamp: {cluster.first_seen_timestamp.isoformat()}\n"
            f"- Wall-Clock Hours Since First Print: {cluster.hours_since_first_print}h\n"
            f"- Active Market Hours Since First Print: {cluster.market_hours_since_first_print}h\n"
            f"- Syndication Count: {len(cluster.articles)} articles across {cluster.unique_sources_count} sources\n"
            f"- Pre-Catalyst Anchor Price: ${cluster.anchor_price_at_first_print:.2f}\n"
            f"- Current Price: ${cluster.current_price:.2f}\n"
            f"- Cumulative Price Drift Since First Print: {cluster.price_drift_since_catalyst_pct:+.2f}% "
            f"({cluster.price_drift_in_atr_units:.2f}x Daily ATR)\n"
            f"- Representative Articles in This Cluster:"
        )
        # Rank articles inside the cluster by source authority so Strategist reads the best 3
        top_arts = sorted(
            cluster.articles,
            key=lambda a: SOURCE_TIER_RANK.get(a.source.lower(), 4),
        )[:max_articles_per_cluster]

        for art in top_arts:
            lead = _extract_lead_text(art, max_body_chars=500)
            lines.append(
                f"  * [ID: {art.article_id}] ({art.source} | {art.published_at.isoformat()}): {lead}"
            )
        lines.append("")

    return "\n".join(lines)


Connecting the Cluster Metrics to the Airlock
When Strategist returns cited_article_ids, your Python airlock maps those IDs back to their parent EventCluster so Manager receives the true age and drift of the cited cluster—even if Strategist cited a syndicated follow-up article:
def resolve_cited_cluster_metrics(
    cited_article_ids: List[str],
    clusters: List[EventCluster],
) -> Tuple[float, float]:
    """
    Finds the EventClusters containing Strategist's cited articles and returns
    (effective_hours_since_first_print, price_drift_since_catalyst_pct)
    for the primary cited cluster.
    """
    cited_set = set(cited_article_ids)
    matched_clusters = [
        c for c in clusters
        if any(art.article_id in cited_set for art in c.articles)
    ]
    if not matched_clusters:
        return 0.0, 0.0

    # Pick the cluster with the most cited articles
    primary_cluster = max(
        matched_clusters,
        key=lambda c: sum(1 for a in c.articles if a.article_id in cited_set),
    )

    # Use market_hours_since_first_print if the event broke over a weekend/overnight
    # so Friday 4:15 PM news evaluated Monday morning isn't penalized as '65h stale',
    # but cap the discount once at least 1 full trading day (6.5m hours) has passed.
    if primary_cluster.market_hours_since_first_print < 6.5:
        effective_age_hours = primary_cluster.market_hours_since_first_print
    else:
        effective_age_hours = primary_cluster.hours_since_first_print

    return effective_age_hours, primary_cluster.price_drift_since_catalyst_pct



User prompt: Put these responses into multiple markdown files
Response: Here is how you can organize the architecture, prompts, and Python modules into multiple distinct files for your repository.
Below is the recommended file structure and the complete code/markdown contents for each file:
Directory Tree Overview
your_trading_bot/
├── docs/
│   └── agentic_trading_improvements.md
└── src/
    ├── strategist_prompt.py
    ├── airlock_middleware.py
    ├── manager_prompt.py
    ├── event_cluster_engine.py
    └── alpaca_price_anchor.py


File 1: docs/agentic_trading_improvements.md
# Agentic Options Trading Strategy - Consistency & Architecture Upgrades

## Summary of Core Upgrades
1. **Neuro-Symbolic Airlock:** Strips narrative prose between agents to prevent `Manager` from anchoring on `Strategist`'s story.
2. **Deterministic Binary Rubrics:** Replaces uncalibrated LLM confidence floats with verifiable pass/fail audits.
3. **Event Clustering & Drift Tracking:** Deduplicates syndicated news rewrites and anchors baseline prices to pre-catalyst regular-session closes to prevent stale-news chasing.
4. **ATR & Delta Scaling:** Replaces fixed % OTM strikes and 1-contract sizing with normalized risk parameters.


File 2: src/strategist_prompt.py
from enum import Enum
from typing import List
from pydantic import BaseModel, Field

class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"

class HorizonBucket(str, Enum):
    SHORT_12_20_DTE = "12_20"
    MEDIUM_20_30_DTE = "20_30"
    STANDARD_30_45_DTE = "30_45"
    LONG_45_60_DTE = "45_60"

class CatalystCategory(str, Enum):
    EARNINGS_OR_GUIDANCE = "earnings_or_guidance"
    REGULATORY_OR_CLINICAL = "regulatory_or_clinical"
    CONTRACT_OR_PARTNERSHIP = "contract_or_partnership"
    MA_OR_ACTIVIST = "ma_or_activist"
    ANALYST_OR_SENTIMENT = "analyst_or_sentiment"
    MACRO_OR_SECTOR_SPILLOVER = "macro_or_sector_spillover"

class StrategistBinaryRubric(BaseModel):
    is_primary_or_tier1_source: bool = Field(
        description="True ONLY if the core catalyst originates from an official filing, company release, regulatory agency, or tier-1 wire (Reuters, Bloomberg, WSJ, CNBC)—False if only blogs, opinion pieces, or unverified chatter."
    )
    has_quantified_financial_impact: bool = Field(
        description="True ONLY if the cited articles contain concrete numbers (e.g., revenue/EPS guidance %, contract dollar value, margin shift, price target delta) rather than vague qualitative commentary."
    )
    is_single_company_direct_driver: bool = Field(
        description="True if the catalyst directly changes THIS specific company's fundamentals; False if the stock is merely mentioned as a sympathy play on macro or sector news."
    )
    is_unanimously_directional: bool = Field(
        description="True ONLY if the cited articles point in a single clear direction without major offsetting bad news (e.g., a revenue beat paired with a guidance cut is False)."
    )
    is_multi_day_structural_catalyst: bool = Field(
        description="True if the fundamental shift is large enough to drive institutional repositioning over 3 to 15 trading days; False if it is a fleeting 1-day headline."
    )
    survives_contradiction_stress_test: bool = Field(
        description="True ONLY if the risks_and_contradictions listed above do NOT invalidate or overshadow the primary directional thesis."
    )

class StrategistOutput(BaseModel):
    cited_article_ids: List[str] = Field(
        description="Exact IDs of stored articles from the input prompt that directly support the thesis."
    )
    risks_and_contradictions: List[str] = Field(
        description="Specific counter-arguments, conflicting data points in the articles, or reasons this trade could fail."
    )
    thesis_for_autopsy_log: str = Field(
        description="Concise 2-3 sentence fundamental thesis citing specific facts. Used ONLY for post-trade audit logs."
    )
    rubric: StrategistBinaryRubric
    catalyst_category: CatalystCategory
    expected_direction: Direction
    recommended_horizon: HorizonBucket
    decision: str = Field(description="Must be 'buy' or 'do_not_buy'.")

STRATEGIST_SYSTEM_PROMPT = """
You are the Fundamental Catalyst Strategist for a systematic US equity options desk.
Your job is to evaluate scraped news articles for a single stock and complete a strict factual audit.

CRITICAL OPERATING RULES:
1. GROUNDING ONLY: You may only cite article IDs explicitly provided in the user prompt. Never invent or extrapolate facts not present in the text.
2. ADVERSARIAL ORDERING: You must populate `risks_and_contradictions` BEFORE answering the boolean `rubric`. Actively look for reasons the news is stale, already priced in, syndicated re-reporting, or contradicted by other details in the text.
3. STRICT BOOLEAN RUBRIC: Treat every field in `rubric` as a strict pass/fail audit. When evidence is ambiguous or missing, you MUST set the boolean to `false`.
4. DO NOT SCORE CONFIDENCE: Do not estimate numerical probabilities. Your boolean rubric answers will be scored deterministically by downstream code.
5. SYNDICATION TRAP WARNING: Check the `hours_since_first_print` and `price_drift_since_first_print_pct` provided in the prompt metadata. If multiple articles repeat the same event that broke over 24 hours ago and the stock has already moved significantly, set `survives_contradiction_stress_test` to `false`.
"""


File 3: src/airlock_middleware.py
from dataclasses import dataclass
from typing import Dict, Set, Optional
from strategist_prompt import StrategistOutput, Direction

RUBRIC_WEIGHTS: Dict[str, float] = {
    "is_primary_or_tier1_source": 0.20,
    "has_quantified_financial_impact": 0.20,
    "is_single_company_direct_driver": 0.15,
    "is_unanimously_directional": 0.15,
    "is_multi_day_structural_catalyst": 0.15,
    "survives_contradiction_stress_test": 0.15,
}

MIN_PASSING_SCORE = 0.70

@dataclass(frozen=True)
class SanitizedCatalystSignal:
    symbol: str
    proposed_direction: str
    catalyst_category: str
    recommended_horizon: str
    deterministic_rubric_score: float
    verified_citation_count: int
    hours_since_first_print: float
    price_drift_since_catalyst_pct: float

def evaluate_and_sanitize_strategist(
    symbol: str,
    strategist_out: StrategistOutput,
    valid_article_ids: Set[str],
    hours_since_first_print: float,
    price_drift_since_catalyst_pct: float,
) -> Optional[SanitizedCatalystSignal]:
    if strategist_out.decision != "buy" or strategist_out.expected_direction == Direction.NEUTRAL:
        return None

    verified_ids = [aid for aid in strategist_out.cited_article_ids if aid in valid_article_ids]
    if not verified_ids:
        return None

    rubric_dict = strategist_out.rubric.model_dump()
    if not rubric_dict["survives_contradiction_stress_test"]:
        return None
    if not (rubric_dict["is_primary_or_tier1_source"] or rubric_dict["has_quantified_financial_impact"]):
        return None

    score = round(
        sum(weight for key, weight in RUBRIC_WEIGHTS.items() if rubric_dict.get(key, False)),
        4
    )
    if score < MIN_PASSING_SCORE:
        return None

    return SanitizedCatalystSignal(
        symbol=symbol,
        proposed_direction=strategist_out.expected_direction.value,
        catalyst_category=strategist_out.catalyst_category.value,
        recommended_horizon=strategist_out.recommended_horizon.value,
        deterministic_rubric_score=score,
        verified_citation_count=len(verified_ids),
        hours_since_first_print=round(hours_since_first_print, 2),
        price_drift_since_catalyst_pct=round(price_drift_since_catalyst_pct, 2),
    )


File 4: src/manager_prompt.py
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from strategist_prompt import HorizonBucket
from airlock_middleware import SanitizedCatalystSignal

class ManagerAction(str, Enum):
    CALL = "call"
    PUT = "put"
    NEITHER = "neither"

class TechnicalRiskChecklist(BaseModel):
    vwap_and_trend_aligned: bool = Field(
        description="True ONLY if price and intraday VWAP/daily trend agree with `proposed_direction`."
    )
    volume_and_rs_confirmed: bool = Field(
        description="True ONLY if relative volume (RVOL >= 1.2) and relative strength vs SPY/sector confirm institutional participation in `proposed_direction`."
    )
    not_overextended_rsi_or_atr: bool = Field(
        description="True ONLY if RSI is not extreme (<72 for calls, >28 for puts) AND intraday move / `price_drift_since_catalyst_pct` is <= 1.5x daily ATR."
    )
    iv_is_acceptable_for_long_option: bool = Field(
        description="True ONLY if IV Percentile is <= 70% (or <= 80% when `deterministic_rubric_score` == 1.0)."
    )
    evidence_is_fresh: bool = Field(
        description="True ONLY if `hours_since_first_print` fits the DTE horizon and price has not already stalled after the initial impulse."
    )

class ManagerOutput(BaseModel):
    technical_checklist: TechnicalRiskChecklist
    technical_audit_note: str = Field(
        description="1-2 sentence explanation citing specific technical numbers (RSI, ATR multiple, IV%, VWAP) that drove the decision."
    )
    selected_dte_bucket: Optional[HorizonBucket] = Field(
        description="Select '12_20', '20_30', '30_45', or '45_60' if trading; null if action is 'neither'."
    )
    action: ManagerAction

MANAGER_SYSTEM_PROMPT = """
You are the Risk & Execution Manager for a systematic US equity options desk.
You do NOT generate trade ideas and you are intentionally blinded to the news story prose to prevent narrative bias.

Your sole role is to act as an adversarial technical and volatility filter on a candidate signal.
"""

def enforce_manager_consistency(
    signal: SanitizedCatalystSignal,
    manager_out: ManagerOutput
) -> ManagerOutput:
    checks = manager_out.technical_checklist.model_dump()
    if not all(checks.values()):
        manager_out.action = ManagerAction.NEITHER
        manager_out.selected_dte_bucket = None
        return manager_out

    if signal.proposed_direction == "bullish" and manager_out.action != ManagerAction.CALL:
        manager_out.action = ManagerAction.NEITHER
        manager_out.selected_dte_bucket = None
    elif signal.proposed_direction == "bearish" and manager_out.action != ManagerAction.PUT:
        manager_out.action = ManagerAction.NEITHER
        manager_out.selected_dte_bucket = None

    return manager_out


File 5: src/event_cluster_engine.py
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Callable, Dict, List, Optional, Set
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

FINANCIAL_STOPWORDS: Set[str] = {
    "the", "and", "for", "that", "this", "with", "from", "its", "are", "was",
    "has", "have", "had", "will", "would", "could", "should", "inc", "corp",
    "ltd", "plc", "shares", "stock", "stocks", "company", "market", "markets",
    "trading", "today", "monday", "tuesday", "wednesday", "thursday", "friday",
    "report", "reports", "reported", "news", "update", "why", "what", "how",
}

SOURCE_TIER_RANK: Dict[str, int] = {
    "sec_filing": 1, "pr_newswire": 1, "business_wire": 1, "globenewswire": 1,
    "reuters": 1, "bloomberg": 1, "wsj": 1, "cnbc": 1, "dow_jones": 1,
    "benzinga": 2, "marketwatch": 2, "barrons": 2, "the_fly": 2,
    "yahoo_finance": 3, "seeking_alpha": 3, "motley_fool": 3, "zacks": 3,
}

@dataclass(frozen=True)
class RawArticle:
    article_id: str
    symbol: str
    headline: str
    summary_or_body: str
    source: str
    published_at: datetime

@dataclass
class EventCluster:
    cluster_id: str
    symbol: str
    articles: List[RawArticle] = field(default_factory=list)
    first_seen_timestamp: Optional[datetime] = None
    latest_timestamp: Optional[datetime] = None
    primary_article: Optional[RawArticle] = None
    hours_since_first_print: float = 0.0
    market_hours_since_first_print: float = 0.0
    anchor_price_at_first_print: Optional[float] = None
    current_price: Optional[float] = None
    price_drift_since_catalyst_pct: float = 0.0
    price_drift_in_atr_units: float = 0.0

    @property
    def unique_sources_count(self) -> int:
        return len({a.source.lower() for a in self.articles})

def _extract_lead_text(article: RawArticle, max_body_chars: int = 400) -> str:
    lead = (article.summary_or_body or "")[:max_body_chars]
    return f"{article.headline} {lead}"

def _tokenize_words(text: str, symbol: str) -> List[str]:
    sym_lower = symbol.lower()
    raw_tokens = re.findall(r"[a-zA-Z0-9$%.\-]+", text.lower())
    cleaned = []
    for tok in raw_tokens:
        tok_stripped = tok.strip(".-")
        if len(tok_stripped) <= 2 or tok_stripped == sym_lower or tok_stripped in FINANCIAL_STOPWORDS:
            continue
        cleaned.append(tok_stripped)
    return cleaned

def _extract_fact_anchors(text: str, symbol: str) -> Set[str]:
    anchors: Set[str] = set()
    for match in re.findall(r"\$\d+(?:\.\d+)?[mbk]?|\d+(?:\.\d+)?%", text.lower()):
        anchors.add(match)
    for match in re.findall(r"\b[A-Z]{3,6}\b", text):
        if match.upper() != symbol.upper() and match.lower() not in FINANCIAL_STOPWORDS:
            anchors.add(match.lower())
    return anchors

def compute_regular_market_hours_elapsed(start_dt: datetime, end_dt: datetime) -> float:
    s_ny = start_dt.astimezone(NY_TZ)
    e_ny = end_dt.astimezone(NY_TZ)
    if e_ny <= s_ny:
        return 0.0

    total_seconds = 0.0
    cur_date = s_ny.date()
    end_date = e_ny.date()

    while cur_date <= end_date:
        if cur_date.weekday() < 5:
            sess_open = datetime.combine(cur_date, MARKET_OPEN, tzinfo=NY_TZ)
            sess_close = datetime.combine(cur_date, MARKET_CLOSE, tzinfo=NY_TZ)
            interval_start = max(s_ny, sess_open)
            interval_end = min(e_ny, sess_close)
            if interval_end > interval_start:
                total_seconds += (interval_end - interval_start).total_seconds()
        cur_date += timedelta(days=1)

    return round(total_seconds / 3600.0, 2)


File 6: src/alpaca_price_anchor.py
from datetime import datetime, timedelta
from typing import Optional
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")

def get_pre_catalyst_regular_session_price(
    data_client: StockHistoricalDataClient,
    symbol: str,
    first_seen_timestamp: datetime,
) -> Optional[float]:
    target_ny = first_seen_timestamp.astimezone(NY_TZ)
    start_lookback = target_ny - timedelta(days=5)

    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(5, TimeFrameUnit.Minute),
        start=start_lookback,
        end=target_ny,
        adjustment="all",
    )
    bars_resp = data_client.get_stock_bars(req)
    symbol_bars = bars_resp.data.get(symbol, [])
    if not symbol_bars:
        return None

    regular_bars = []
    for bar in symbol_bars:
        bar_ny = bar.timestamp.astimezone(NY_TZ)
        if bar_ny.weekday() < 5 and (9, 30) <= (bar_ny.hour, bar_ny.minute) <= (16, 0):
            if bar_ny <= target_ny:
                regular_bars.append(bar)

    if not regular_bars:
        return float(symbol_bars[-1].close)

    return float(regular_bars[-1].close)




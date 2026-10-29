"""Split a manager decision's outcome into evidence quality, direction and expression. Monitoring only.

All functions are pure: the frozen decision, its checks and the daily closes are passed in. Evidence asks whether the
thesis was grounded, direction whether the stock moved as called, expression whether the option trade captured it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from random import Random
from statistics import stdev
from typing import Any

from agent_helpers.deterministic_option_selector.market import _get_reference_stock_price
from catalyst_models import timestamp
from services.llm_direction_score import DIRECTION_SIGN, HORIZONS, returns_from_closes

ATTRIBUTION_VERSION = 'decision-attribution-v2'
PRIMARY_HORIZON = 5
DIRECTION_BAND = 0.005
# Calendar slack on top of a horizon's trading days (weekends are in the 7/5 factor) for holidays and late bars.
HORIZON_GRACE_DAYS = 4
# Closes or a trade's P&L can arrive late (a fetch outage); keep re-checking this long past the longest horizon.
RECHECK_DAYS = 30
STRONG_RESOLVABLE_SHARE = 0.9
WEAK_RESOLVABLE_SHARE = 0.5
MIN_INDEPENDENT_ROOTS = 2
MAX_FRESH_AGE_HOURS = 72
BOOTSTRAP_RESAMPLES = 1000
CENTRAL_FAILURES = ('central_claim_failed', 'package_opposing_evidence_unaddressed')
CHECK_QUALITY = {'validated': 'strong', 'validated_with_warnings': 'adequate', 'deferred': 'weak', 'invalid_output': 'weak'}
SUBMITTED_EXIT_PREFIX = 'managed_exit_submitted'
OPEN_POSITION_PREFIX = 'open_'


def evidence_layer(strategist, thesis_check, catalysts, articles, decided_at) -> dict[str, Any]:
    """`{quality, source, signals}`. A Phase 4 thesis check decides when there is one; otherwise a grounding proxy does."""
    if thesis_check and thesis_check.get('status') in CHECK_QUALITY:
        return {'quality': _check_quality(thesis_check), 'source': 'thesis_check',
                'signals': {'status': thesis_check['status'], 'failures': thesis_check.get('failures') or []}}
    if not strategist:
        return {'quality': 'unchecked', 'source': 'grounding_proxy', 'signals': {}}
    signals = _proxy_signals(strategist, catalysts, articles, decided_at)
    return {'quality': _proxy_quality(signals), 'source': 'grounding_proxy', 'signals': {
        **signals, 'model_self_rating': {'evidence_quality': strategist.get('evidence_quality'),
                                         'contradictions_present': strategist.get('contradictions_present')}}}


def _check_quality(check: dict[str, Any]) -> str:
    claims = check.get('claims') or []
    failed_central = any(claim.get('central') and claim.get('status') == 'failed' for claim in claims)
    if failed_central or any(code in (check.get('failures') or []) for code in CENTRAL_FAILURES):
        return 'weak'
    return CHECK_QUALITY[check['status']]


def _proxy_signals(strategist, catalysts, articles, decided_at) -> dict[str, Any]:
    claims = [claim for claim in strategist.get('evidence_claims') or [] if isinstance(claim, dict)]
    cited = {article_id for claim in claims for article_id in claim.get('evidence_article_ids') or []}
    published = {article.get('article_id'): timestamp(article.get('published_at')) for article in articles}
    resolvable = {article_id: published[article_id] for article_id in cited
                  if published.get(article_id) and published[article_id] <= decided_at}
    primary = catalysts[0] if catalysts else {}
    # Same method as news_lag_report: how long before the decision the newest cited article was published.
    newest = max(resolvable.values(), default=None)
    return {'cited_articles': len(cited),
            'cited_articles_resolvable': len(resolvable) / len(cited) if cited else None,
            'catalyst_claim_present': any(claim.get('claim_type') == 'catalyst' for claim in claims),
            'independent_roots': primary.get('independent_root_count') or 0,
            'primary_source_present': bool(primary.get('primary_source_present')),
            'freshest_evidence_age_hours': (decided_at - newest).total_seconds() / 3600 if newest else None}


def _proxy_quality(signals: dict[str, Any]) -> str:
    resolvable = signals['cited_articles_resolvable']
    if not signals['catalyst_claim_present'] or resolvable is None or resolvable < WEAK_RESOLVABLE_SHARE:
        return 'weak'
    age = signals['freshest_evidence_age_hours']
    independent = signals['independent_roots'] >= MIN_INDEPENDENT_ROOTS or signals['primary_source_present']
    if resolvable >= STRONG_RESOLVABLE_SHARE and independent and age is not None and age <= MAX_FRESH_AGE_HOURS:
        return 'strong'
    return 'adequate'


def direction_layer(decision, payload, closes, *, now: datetime | None = None) -> dict[str, Any]:
    """Grade the call against the stock's return from the frozen reference price at each horizon (string keys).

    A horizon stays `pending` until its closes exist, abstentions included, so that `abs_move` is filled in later.
    """
    reference = _get_reference_stock_price(payload.get('market_context') or {})
    returns = returns_from_closes(reference, closes) if reference else {}
    sign = DIRECTION_SIGN.get(str(decision.get('manager_decision') or '').lower())
    elapsed = (now or datetime.now(timezone.utc)) - timestamp(decision['decision_run_at'])
    outcomes = {str(horizon): _horizon_outcome(returns.get(horizon), sign, elapsed, horizon) for horizon in HORIZONS}
    return {'outcome': 'abstained' if sign is None else outcomes[str(PRIMARY_HORIZON)],
            'primary_horizon': PRIMARY_HORIZON,
            'horizon_returns': {str(horizon): value for horizon, value in returns.items()},
            'horizon_outcomes': outcomes,
            'abs_move': {str(horizon): abs(value) for horizon, value in returns.items()},
            'forward_residual_return': decision.get('forward_residual_return')}


def _horizon_outcome(value: float | None, sign: int | None, elapsed: timedelta, horizon: int) -> str:
    if value is None:
        waited_out = elapsed > timedelta(days=ceil(horizon * 7 / 5) + HORIZON_GRACE_DAYS)
        return 'unavailable' if waited_out else 'pending'
    if sign is None:
        return 'abstained'
    signed = sign * value
    if signed > DIRECTION_BAND:
        return 'correct'
    return 'wrong' if signed < -DIRECTION_BAND else 'flat'


def expression_layer(decision, direction, *, final: bool = False) -> dict[str, Any]:
    """Whether an executed trade captured the direction; `pnl_pct` is the realized or submitted-exit P&L.

    `final` means the re-check window has passed: a trade that is not a known open position and still has no
    P&L is then `unavailable` rather than `pending` forever. An open position stays `pending` however long it runs.
    """
    if not decision.get('trade_executed'):
        return {'outcome': 'not_traded', 'pnl_pct': None}
    pnl = _settled_pnl(decision)
    outcome = direction['outcome']
    if pnl is None or outcome == 'pending':
        still_open = str(decision.get('resolved_outcome_label') or '').startswith(OPEN_POSITION_PREFIX)
        label = 'unavailable' if final and outcome != 'pending' and not still_open else 'pending'
    elif outcome == 'correct':
        label = 'captured' if pnl > 0 else 'lost_despite_direction'
    elif outcome == 'wrong':
        label = 'lost_with_direction'
    else:
        label = 'lost_in_flat' if outcome == 'flat' and pnl < 0 else 'no_direction_signal'
    return {'outcome': label, 'pnl_pct': pnl}


def _settled_pnl(decision) -> float | None:
    """Realized P&L, or the latest P&L once the exit has been submitted; an open position has none yet."""
    if decision.get('realized_pnl_pct') is not None:
        return decision['realized_pnl_pct']
    if str(decision.get('resolved_outcome_label') or '').startswith(SUBMITTED_EXIT_PREFIX):
        return decision.get('latest_trade_pnl_pct')
    return None


def attribute(decision, payload, thesis_check, catalysts, closes, *, now: datetime | None = None) -> dict[str, Any]:
    """All three layers for one decision, plus the one layer that failed first.

    `settled` is false while a missing horizon could still be filled in by late closes.
    """
    supporting = payload.get('supporting_articles') or {}
    articles = [*supporting.get('article_summaries', []), *supporting.get('full_articles', [])]
    decided_at = timestamp(decision['decision_run_at'])
    evidence = evidence_layer(payload.get('strategist_recommendation'), thesis_check, catalysts, articles, decided_at)
    direction = direction_layer(decision, payload, closes, now=now)
    final = (now or datetime.now(timezone.utc)) - decided_at > timedelta(
        days=ceil(max(HORIZONS) * 7 / 5) + HORIZON_GRACE_DAYS + RECHECK_DAYS)
    expression = expression_layer(decision, direction, final=final)
    return {'manager_decision_history_id': decision['id'], 'attribution_version': ATTRIBUTION_VERSION,
            'evidence': evidence, 'direction': direction, 'expression': expression,
            'primary_failure': _primary_failure(evidence['quality'], direction['outcome'], expression['outcome']),
            'settled': final or 'unavailable' not in direction['horizon_outcomes'].values()}


def _primary_failure(evidence_quality: str, direction_outcome: str, expression_outcome: str) -> str:
    if direction_outcome == 'wrong':
        return 'evidence' if evidence_quality == 'weak' else 'forecast'
    if direction_outcome == 'pending' or (direction_outcome == 'correct' and expression_outcome == 'pending'):
        return 'pending'
    return 'expression' if expression_outcome in ('lost_despite_direction', 'lost_in_flat') else 'none'


def bootstrap_se(values, *, n: int = BOOTSTRAP_RESAMPLES, seed: int = 0) -> float | None:
    """Standard error of the mean (a hit rate, for 0/1 values) from `n` seeded resamples; None without values."""
    values = list(values)
    if not values:
        return None
    random = Random(seed)
    return stdev(sum(random.choices(values, k=len(values))) / len(values) for _ in range(n))

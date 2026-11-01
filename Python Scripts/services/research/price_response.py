"""Aligned, completed-bar price responses; no provider calls or trading side effects."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import math
from statistics import mean
from zoneinfo import ZoneInfo

from catalyst_models import timestamp
from services.gates.pricing_policy import PricingPolicy


def positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None


def completed_at(bar):
    """Provider daily timestamps denote sessions; hourly timestamps denote starts.

    Daily bars become usable at 16:00 New York (conservative on half days).
    Explicit end timestamps from adapters take precedence.
    """
    if bar.get('available_at'):
        return timestamp(bar['available_at'])
    start = timestamp(bar.get('bar_at'))
    if start is None:
        return None
    if bar.get('timeframe') == '1h':
        return start + timedelta(hours=1)
    return datetime.combine(start.date(), time(16), ZoneInfo('America/New_York')).astimezone(timezone.utc)


def aligned_prices(histories, *, as_of, timeframe='1d'):
    series = []
    for symbol in ('stock', 'market', 'sector'):
        values = {}
        for bar in histories.get(symbol, []):
            end = completed_at(bar)
            close = positive(bar.get('close_price'))
            if bar.get('timeframe', '1d') == timeframe and end and end <= as_of and close:
                values[end] = {**bar, 'close_price': close}
        series.append(values)
    common = sorted(set(series[0]) & set(series[1]) & set(series[2]))
    return [{'at': at, 'prices': [item[at]['close_price'] for item in series],
             'stock_bar': series[0][at]} for at in common]


def fit_betas(histories, *, before, policy=None):
    policy = policy or PricingPolicy.from_env()
    points = aligned_prices(histories, as_of=before)
    points = [point for point in points if point['at'] < before][-(policy.regression_returns + 1):]
    if len(points) != policy.regression_returns + 1:
        return None
    # Avoid fitting 60 scattered prices across a long cache outage.
    if before - points[-1]['at'] > timedelta(days=4) or points[-1]['at'] - points[0]['at'] > timedelta(days=120):
        return None
    returns = [[b / a - 1 for a, b in zip(left['prices'], right['prices'])]
               for left, right in zip(points, points[1:])]
    y, x, z = zip(*returns)
    y0, x0, z0 = mean(y), mean(x), mean(z)
    xx = sum((v - x0) ** 2 for v in x)
    zz = sum((v - z0) ** 2 for v in z)
    xz = sum((a - x0) * (b - z0) for a, b in zip(x, z))
    xy = sum((a - x0) * (b - y0) for a, b in zip(x, y))
    zy = sum((a - z0) * (b - y0) for a, b in zip(z, y))
    determinant = xx * zz - xz * xz
    if xx <= 0 or zz <= 0 or determinant <= 1e-8 * xx * zz:
        return None
    market = (xy * zz - zy * xz) / determinant
    sector = (zy * xx - xy * xz) / determinant
    return {'beta_market': market, 'beta_sector': sector,
            'intercept': y0 - market * x0 - sector * z0, 'n': len(returns),
            'fit_start': points[0]['at'].isoformat(), 'fit_end': points[-1]['at'].isoformat()}


def residual(prices, baseline, betas):
    stock, market, sector = [b / a - 1 for a, b in zip(baseline, prices)]
    return {'stock_return': stock, 'spy_return': market, 'sector_return': sector,
            'residual_return': stock - betas['beta_market'] * market - betas['beta_sector'] * sector}


def measure_response(histories, *, event_at, as_of, horizon_days=None, betas=None, direction='unknown', policy=None):
    """Calendar horizons, next shared close; intraday never falls back to daily.

    Baseline is the last completed close at/before publication, not the first
    post-news price. Endpoint delays and sampling basis are always retained.
    """
    event = timestamp(event_at)
    unknown = {'status': 'missing_event_time', 'residual_return': None}
    if event is None or event > as_of:
        return unknown
    target = event + timedelta(days=horizon_days) if horizon_days is not None else as_of
    if target > as_of:
        return {**unknown, 'status': 'pending_horizon', 'target_at': target.isoformat()}
    betas = betas or fit_betas(histories, before=event, policy=policy)
    if betas is None:
        return {**unknown, 'status': 'missing_regression_history'}
    hourly_only = horizon_days is not None and horizon_days < 1
    for timeframe in (('1h',) if hourly_only else ('1h', '1d')):
        points = aligned_prices(histories, as_of=as_of, timeframe=timeframe)
        prior = [point for point in points if point['at'] <= event]
        if not prior or event - prior[-1]['at'] > timedelta(days=4):
            continue
        baseline = prior[-1]
        if horizon_days is None:
            ends = [point for point in points if event <= point['at'] <= as_of]
            end = ends[-1] if ends else None
            if end and as_of - end['at'] > timedelta(days=4):
                end = None
        else:
            ends = [point for point in points if point['at'] >= target]
            end = ends[0] if ends else None
            max_delay = timedelta(hours=1) if hourly_only else timedelta(days=4)
            if end and end['at'] - target > max_delay:
                end = None
        if end is None:
            continue
        moves = residual(end['prices'], baseline['prices'], betas)
        path = [0.0] + [residual(p['prices'], baseline['prices'], betas)['residual_return']
                        for p in points if baseline['at'] < p['at'] <= end['at']]
        sign = {'positive': 1, 'negative': -1}.get(direction)
        signed_path = [value * sign for value in path] if sign else []
        return {'status': 'complete', 'response_version': (policy or PricingPolicy.from_env()).version, **moves, 'betas': betas,
                'price_at_event': baseline['prices'][0], 'price_at_endpoint': end['prices'][0],
                'baseline_prices': baseline['prices'], 'endpoint_prices': end['prices'],
                'baseline_at': baseline['at'].isoformat(), 'outcome_at': end['at'].isoformat(),
                'target_at': target.isoformat(), 'endpoint_delay_seconds': (end['at'] - target).total_seconds(),
                'baseline_age_seconds': (event - baseline['at']).total_seconds(),
                'sampling': f'{timeframe}_completed_close_proxy',
                'mfe': max(signed_path) if signed_path else None,
                'mae': min(signed_path) if signed_path else None,
                'providers': {key: sorted({str(b.get('provider')) for b in bars})
                              for key, bars in histories.items()}}
    return {**unknown, 'status': 'missing_aligned_prices', 'betas': betas}


def atr_fraction(histories, *, before):
    bars = sorted((bar for bar in histories.get('stock', []) if bar.get('timeframe', '1d') == '1d'
                   and completed_at(bar) and completed_at(bar) < before), key=completed_at)[-15:]
    if len(bars) < 15 or before - completed_at(bars[-1]) > timedelta(days=4):
        return None
    ranges = []
    for prior, bar in zip(bars, bars[1:]):
        high, low, close = (positive(bar.get('high_price')), positive(bar.get('low_price')),
                            positive(prior.get('close_price')))
        if high is None or low is None or close is None or high < low:
            return None
        ranges.append(max(high - low, abs(high - close), abs(low - close)))
    last = positive(bars[-1].get('close_price'))
    return mean(ranges) / last if last else None


def evaluate_price_absorption(state, histories, stats, *, as_of, policy=None):
    policy = policy or PricingPolicy.from_env()
    response = measure_response(histories, event_at=state.get('first_public_time'), as_of=as_of,
                                direction=state.get('direction'), policy=policy)
    expected = positive(stats.get('expected_abs_residual_move')) if stats.get('usable') else None
    atr = atr_fraction(histories, before=timestamp(state.get('first_public_time')) or as_of)
    denominator = expected or atr
    move = response.get('residual_return')
    ratio = abs(move) / denominator if move is not None and denominator else None
    bucket = ('unknown' if ratio is None else 'largely_unexpressed' if ratio < .30 else
              'partially_expressed' if ratio < .80 else 'substantially_expressed' if ratio <= 1.25 else
              'potentially_overabsorbed')
    sign = {'positive': 1, 'negative': -1}.get(state.get('direction'))
    would_allow = None if ratio is None or sign is None else not (ratio > policy.absorption_threshold and move * sign > 0)
    return {'stage': 'PRICE_ABSORPTION', 'allowed': True, 'would_allow': would_allow,
            'enforcement_mode': 'shadow', 'policy_version': policy.version,
            'reason_codes': ['price_response_unknown' if would_allow is None else
                             'already_priced' if not would_allow else 'price_response_pass'],
            'thresholds': {'absorption_ratio': policy.absorption_threshold},
            'metrics': {**response, 'residual_move_since_event': move, 'price_absorption_ratio': ratio,
                        'bucket': bucket, 'historical_expected_abs_move': expected,
                        'atr_fraction': atr, 'normalizing_move': denominator, 'normalization': 'empirical_absolute_move' if expected else 'atr_14',
                        'calibration': 'provisional', 'historical_sample_size': stats.get('n', 0),
                        'stats': stats, 'catalyst_direction': state.get('direction')}}

"""European Black-Scholes repricing for scenario scoring and trade economics.

No dividends and no early exercise: callers decide when those assumptions are unsupported.
Time is in calendar years (days / 365) and volatility is a decimal fraction.
"""
from dataclasses import dataclass
from datetime import datetime, time
import math
from zoneinfo import ZoneInfo

MINIMUM_VOLATILITY = 1e-4
MAXIMUM_VOLATILITY = 5.0
VOLATILITY_TOLERANCE = 1e-7
MAXIMUM_BISECTION_STEPS = 200
DAYS_PER_YEAR = 365
CONTRACT_MULTIPLIER = 100  # shares per US equity option contract
SECONDS_PER_DAY = 86400
MARKET_TIMEZONE = ZoneInfo('America/New_York')
EXPIRATION_CLOSE = time(16)


def normal_cdf(value):
    return .5 * (1 + math.erf(value / math.sqrt(2)))


def intrinsic_value(side, *, spot, strike):
    return max(0.0, spot - strike) if side == 'call' else max(0.0, strike - spot)


def black_scholes_price(side, *, spot, strike, years, volatility, rate):
    if years <= 0 or volatility <= 0:
        return intrinsic_value(side, spot=spot, strike=strike)
    root = volatility * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + volatility ** 2 / 2) * years) / root
    d2 = d1 - root
    discounted_strike = strike * math.exp(-rate * years)
    if side == 'call':
        return spot * normal_cdf(d1) - discounted_strike * normal_cdf(d2)
    return discounted_strike * normal_cdf(-d2) - spot * normal_cdf(-d1)


def implied_volatility(side, price, *, spot, strike, years, rate):
    """Bisection on the monotonic price/volatility curve; None when no volatility fits."""
    if years <= 0 or price <= 0 or spot <= 0 or strike <= 0:
        return None

    def priced(volatility):
        return black_scholes_price(side, spot=spot, strike=strike, years=years, volatility=volatility, rate=rate)

    low, high = MINIMUM_VOLATILITY, MAXIMUM_VOLATILITY
    if not priced(low) <= price <= priced(high):
        return None
    for _ in range(MAXIMUM_BISECTION_STEPS):
        middle = (low + high) / 2
        if priced(middle) < price:
            low = middle
        else:
            high = middle
        if high - low < VOLATILITY_TOLERANCE:
            break
    return (low + high) / 2


def option_side(contract_type):
    """'call' or 'put' from plain text or an enum rendering such as 'ContractType.CALL'."""
    return str(contract_type or '').lower().rsplit('.', 1)[-1]


def expiration_close(expiration_date):
    try:
        day = datetime.strptime(str(expiration_date or ''), '%Y-%m-%d').date()
    except ValueError:
        return None
    return datetime.combine(day, EXPIRATION_CLOSE, MARKET_TIMEZONE)


def days_until_expiration(expiration_date, *, as_of):
    close = expiration_close(expiration_date)
    return (close - as_of).total_seconds() / SECONDS_PER_DAY if close else None


@dataclass(frozen=True)
class CalibratedOption:
    """A European option priced with the volatility that reproduces its quote mid today."""
    side: str
    spot: float
    strike: float
    years: float
    volatility: float
    rate: float

    @classmethod
    def from_mid(cls, side, mid, *, spot, strike, years, rate):
        volatility = implied_volatility(side, mid, spot=spot, strike=strike, years=years, rate=rate)
        return None if volatility is None else cls(side, spot, strike, years, volatility, rate)

    def value_after(self, days, *, move=0.0, volatility_change=0.0):
        """Model value `days` from now, after a fractional spot `move` and a volatility shift."""
        return black_scholes_price(self.side, spot=self.spot * (1 + move), strike=self.strike,
                                   years=self.years - days / DAYS_PER_YEAR,
                                   volatility=self.volatility + volatility_change, rate=self.rate)

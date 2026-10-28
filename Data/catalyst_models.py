"""Deterministic catalyst capture taxonomy and uncalibrated decay priors."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

CLASSIFICATION_VERSION = 'catalyst-rules-v2'
LABEL_VERSION = 'catalyst-llm-v1'
LABELED_CLASSIFICATION_VERSION = CLASSIFICATION_VERSION + '+' + LABEL_VERSION
PRIMARY_SOURCE_TYPES = frozenset({'primary_filing', 'company_release', 'government', 'court_document', 'earnings_call'})
CATALYST_TYPES = ('earnings', 'guidance', 'ma', 'regulatory', 'legal', 'product', 'customer_contract',
                 'supply_chain', 'management', 'capital_return', 'financing', 'analyst_action',
                 'macro', 'geopolitical', 'industry_readthrough', 'rumor', 'other')
# An unrecognised or unconfirmed event has no known reach. Scope weighting relaxes regime
# vetoes, so these must never default to the most lenient (idiosyncratic) scope.
UNSCOPED_TYPES = frozenset({'rumor', 'other'})
CATALYST_FAMILIES = ('earnings_positive', 'earnings_negative', 'guidance_raise', 'guidance_cut',
                    'regulatory_positive', 'regulatory_negative', 'legal_positive', 'legal_negative',
                    'ma', 'product_commercial', 'analyst', 'macro_sector', 'other')
HORIZONS = ('intraday', '1_3_days', '4_10_days', '2_4_weeks', '1_2_months')
# Calendar-day research priors, deliberately explicit about overnight/weekend decay.
# M&A/rumor/unknown have no universal decay prior; do not invent one.
HALF_LIFE_MINUTES = {'analyst_action': 1440, 'earnings': 4320, 'guidance': 7200,
                     'legal': 8640, 'regulatory': 12960, 'product': 7200,
                     'customer_contract': 7200, 'macro': 1440, 'industry_readthrough': 2880}
DECAY_BASIS = 'elapsed_calendar_minutes'


def timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class CatalystState:
    catalyst_id: str
    ticker: str | None
    catalyst_type: str
    catalyst_subtype: str
    catalyst_family: str
    catalyst_scope: str
    direction: str
    mechanism: str
    expected_horizon: str
    first_public_time: str | None
    first_seen_time: str | None
    latest_evidence_time: str | None
    latest_root_event_time: str | None
    article_count: int
    source_count: int
    independent_root_count: int
    primary_source_present: bool
    evidence_score: float | None
    root_event_ids: list[str]
    expected_half_life_minutes: int | None
    catalyst_age_minutes: float | None = None
    sector: str | None = None
    industry: str | None = None
    novelty_score: float | None = None
    materiality_score: float | None = None
    price_at_first_public: float | None = None
    price_at_first_seen: float | None = None
    price_now: float | None = None
    raw_move_since_event: float | None = None
    residual_move_since_event: float | None = None
    historical_expected_move: float | None = None
    historical_move_std: float | None = None
    historical_sample_size: int | None = None
    classification_version: str = CLASSIFICATION_VERSION
    classification_status: str = 'classified'
    classification_source: str = 'rules'


def detect_catalyst(title, summary=''):
    # Headline anchors the event; incidental body mentions must not replace it.
    text = str(title or summary or '').lower()
    rules = (
        ('rumor', r'\b(rumou?r|unconfirmed|reportedly)\b'),
        ('guidance', r'\b(guidance|outlook|forecast)\b'),
        ('earnings', r'\b(earnings|eps|quarterly results|revenue beat|revenue miss)\b'),
        ('ma', r'\b(merger|acquisition|acquire[sd]?|buyout|takeover)\b'),
        ('regulatory', r'\b(fda|regulator\w*|antitrust|approval|enforcement)\b'),
        ('legal', r'\b(court|lawsuit|litigation|ruling|settlement)\b'),
        ('analyst_action', r'\b(analyst|upgrade\w*|downgrade\w*|price target)\b'),
        ('customer_contract', r'\b(contract|customer agreement|order backlog)\b'),
        ('product', r'\b(launch\w*|new product|new chip|product release)\b'),
        ('supply_chain', r'\b(supply chain|shortage|factory|production halt)\b'),
        ('management', r'\b(ceo|cfo|chief executive|chief financial|resign\w*)\b'),
        ('capital_return', r'\b(buyback|dividend|repurchase)\b'),
        ('financing', r'\b(financing|debt offering|share offering|capital raise)\b'),
        ('macro', r'\b(federal reserve|inflation|interest rates|jobs report|gdp)\b'),
        ('geopolitical', r'\b(tariff|sanction|war|ceasefire)\b'),
        ('industry_readthrough', r'\b(sector|industry outlook|peer results)\b'),
    )
    kind = next((kind for kind, pattern in rules if re.search(pattern, text)), 'other')
    positive = bool(re.search(r'\b(raise[sd]?|beat[s]?|upgrade\w*|approv\w*|win[s]?|favorable|record)\b', text))
    negative = bool(re.search(r'\b(cut[s]?|lower\w*|miss\w*|downgrade\w*|reject\w*|deni\w*|loss|adverse)\b', text))
    direction = 'mixed' if positive and negative else 'positive' if positive else 'negative' if negative else 'unknown'
    return kind, direction, text


def derive_classification(kind, direction, text):
    subtype = direction if direction in {'positive', 'negative'} else 'unspecified'
    if kind == 'guidance':
        subtype = ('raise' if direction == 'positive' else 'cut' if direction == 'negative' else
                   next((word for word in ('withdraw', 'reiterate', 'initiate') if word in text), 'unspecified'))
        metric = next((word for word in ('revenue', 'margin', 'eps', 'bookings') if word in text), None)
        if metric:
            subtype += '_' + metric
    family = 'other'
    if kind in {'earnings', 'regulatory', 'legal'} and direction in {'positive', 'negative'}:
        family = kind + '_' + direction
    elif kind == 'guidance' and direction in {'positive', 'negative'}:
        family = 'guidance_raise' if direction == 'positive' else 'guidance_cut'
    elif kind == 'ma':
        family = 'ma'
    elif kind in {'product', 'customer_contract'}:
        family = 'product_commercial'
    elif kind == 'analyst_action':
        family = 'analyst'
    elif kind in {'macro', 'geopolitical', 'industry_readthrough'}:
        family = 'macro_sector'
    scope = ('unknown' if kind in UNSCOPED_TYPES else 'systemic' if kind in {'macro', 'geopolitical'}
             else 'sector' if kind == 'industry_readthrough' else 'idiosyncratic')
    horizon = '1_3_days' if kind in {'analyst_action', 'macro'} else '1_2_months' if kind == 'ma' else '4_10_days'
    mechanisms = {
        'guidance': 'Revision to forward revenue or profitability expectations',
        'earnings': 'Reported performance changes earnings expectations',
        'ma': 'Deal consideration, financing and completion probability affect value',
        'regulatory': 'Changes to permitted operations or market access',
        'legal': 'Changes to legal liabilities or business restrictions',
        'analyst_action': 'Revised external estimates or valuation assessment',
        'product': 'Potential changes to future product revenue and costs',
        'customer_contract': 'Changes to contracted revenue and demand visibility',
        'supply_chain': 'Changes to input availability and production costs',
        'management': 'Changes to leadership and execution expectations',
        'capital_return': 'Changes to shareholder distributions or share count',
        'financing': 'Changes to liquidity, leverage or dilution',
        'macro': 'Changes to demand, financing conditions or discount rates',
        'geopolitical': 'Changes to trade access, demand or risk expectations',
        'industry_readthrough': 'Related-company evidence may change sector expectations',
        'rumor': 'Unconfirmed event; transmission mechanism needs review',
        'other': 'Unclassified transmission mechanism; review required',
    }
    return dict(catalyst_type=kind, catalyst_subtype=subtype, catalyst_family=family, catalyst_scope=scope,
                direction=direction, mechanism=mechanisms[kind],
                expected_horizon=horizon, classification_status='needs_review' if family == 'other' else 'classified')


def classify_catalyst(title, summary=''):
    return derive_classification(*detect_catalyst(title, summary))

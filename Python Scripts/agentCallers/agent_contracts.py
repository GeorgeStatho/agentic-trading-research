from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class RankingEntry(TypedDict):
    sector_key: NotRequired[str]
    industry_key: NotRequired[str]
    score: int


class RankingsPayload(TypedDict):
    top_sectors: list[RankingEntry]
    top_industries_by_sector: dict[str, list[RankingEntry]]
    ranking_max_age_days: int | None


class IndustrySummary(TypedDict):
    industry_id: int
    industry_key: str
    industry_name: str
    sector_key: str
    sector_name: str
    symbol: Any
    market_weight: Any


class CompanySummary(TypedDict):
    company_id: int
    symbol: str
    name: str
    rating: Any
    market_weight: Any


class RankedCompanySummary(CompanySummary):
    rank: int
    ranking_type: str


class SelectedCompany(CompanySummary):
    source_groups: list[str]


class IndustryCompanyGroupsPayload(TypedDict):
    industry: IndustrySummary
    top_companies: list[CompanySummary]
    top_growth_companies: list[RankedCompanySummary]
    top_performing_companies: list[RankedCompanySummary]


class IndustryCompanySelectionPayload(TypedDict):
    industry: IndustrySummary
    selected_companies: list[SelectedCompany]


class CompanyOpportunistSummaryCompany(TypedDict):
    company_id: Any
    symbol: Any
    name: Any
    industry_key: Any
    sector_key: Any


class CompanyOpportunistSummaryPayload(TypedDict):
    company: CompanyOpportunistSummaryCompany
    impact_count: int
    confidence_counts: dict[str, int]
    direction_counts: dict[str, int]
    magnitude_counts: dict[str, int]
    sample_reasons: list[str]


class PipelineTargetsPayload(TypedDict):
    rankings: RankingsPayload
    ranking_max_age_days: int | None
    top_sector_keys: list[str]
    top_industry_keys: list[str]
    selected_companies: list[SelectedCompany]
    selected_company_ids: list[int]
    selected_company_symbols: list[str]


class PipelineIndustryResult(TypedDict):
    industry: IndustrySummary
    selected_companies: list[SelectedCompany]
    company_opportunist_summaries: list[CompanyOpportunistSummaryPayload]


class PipelineSectorResult(TypedDict):
    sector_key: str
    top_industries: list[RankingEntry]
    sector_opportunist_result: Any
    industry_opportunist_result: Any
    industries: list[PipelineIndustryResult]


class ScrapeResultsPayload(TypedDict):
    macro_news: dict[str, int]
    sectors: dict[str, int]
    industries: dict[str, int]
    companies: dict[str, int]


class PipelineRunRankingsPayload(TypedDict):
    before_sector_stage: RankingsPayload
    after_pipeline: RankingsPayload


class PipelineRunResult(TypedDict):
    top_sector_count: int
    top_industry_count: int
    top_company_count: int
    ranking_max_age_days: int | None
    pipeline_mode: str
    macro_news_to_sectors: dict[str, list[Any]]
    scrape_results: ScrapeResultsPayload
    rankings: PipelineRunRankingsPayload
    sectors: list[PipelineSectorResult]


class PayloadFilters(TypedDict):
    start_time: str
    end_time: str
    max_age_days: int | None
    summary_article_limit: NotRequired[int]
    full_article_limit: NotRequired[int]
    macro_confidence: str
    sector_confidence: NotRequired[str]
    industry_confidence: NotRequired[str]
    company_confidence: NotRequired[str]
    sector_news_source: str


class OpportunistSectorPayload(TypedDict):
    sector_id: int
    sector_key: str
    sector_name: str


class OpportunistInputPayload(TypedDict):
    sector: OpportunistSectorPayload
    filters: PayloadFilters
    sector_rss_news: list[dict[str, Any]]
    related_macro_news: list[dict[str, Any]]


class HistoricalPricePoint(TypedDict):
    timestamp: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None


class HistoricalPriceSnapshot(TypedDict):
    available: bool
    symbol: str
    period: str
    interval: str
    point_count: int
    first_timestamp: str
    last_timestamp: str
    first_close: float | None
    last_close: float | None
    absolute_change: float | None
    percent_change: float | None
    period_high: float | None
    period_low: float | None
    total_volume: int | None
    recent_points: list[HistoricalPricePoint]
    error: str


TrendQualityPayload = TypedDict(
    "TrendQualityPayload",
    {
        "source": str,
        "annualized": bool,
        "risk_free_rate": float,
        "trading_periods_per_year": int,
        "5d_sharpe": float | None,
        "1m_sharpe": float | None,
        "3m_sharpe": float | None,
        "5d_return_pct": float | None,
        "1m_return_pct": float | None,
        "3m_return_pct": float | None,
    },
)


class CompanyScopePayload(TypedDict):
    company_id: int
    symbol: str
    name: str
    industry_id: int
    industry_key: str
    industry_name: str
    sector_id: int
    sector_key: str
    sector_name: str
    rating: str
    market_weight: Any
    market_data: dict[str, Any]
    historical_price_data: dict[str, HistoricalPriceSnapshot]
    trend_quality: TrendQualityPayload


class AgentSignalPayload(TypedDict):
    layer: str
    article_id: int
    confidence: str
    reason: str
    published_at: str
    source: str
    source_url: str
    title: str
    summary: str
    impact_direction: NotRequired[str]
    impact_magnitude: NotRequired[str]
    materiality: NotRequired[str]
    time_horizon: NotRequired[str]
    effect_type: NotRequired[str]
    relative_positioning: NotRequired[str]
    news_scope: NotRequired[str]
    sector_name: NotRequired[str]
    industry_name: NotRequired[str]
    company_name: NotRequired[str]


class ViewPayload(TypedDict):
    layer: str
    label: str
    count: int
    agent_conclusions: list[AgentSignalPayload]


class StrategistViewsPayload(TypedDict):
    macro_view: ViewPayload
    sector_view: ViewPayload
    industry_view: ViewPayload
    company_view: ViewPayload


class SupportingArticleSummaryPayload(TypedDict):
    article_id: int
    title: str
    summary: str
    source: str
    source_url: str
    published_at: str
    article_scope: str
    evidence_layers: list[str]
    agent_signals: list[AgentSignalPayload]


class SupportingFullArticlePayload(SupportingArticleSummaryPayload):
    body: str


class SupportingArticlesPayload(TypedDict):
    article_summaries: list[SupportingArticleSummaryPayload]
    full_articles: list[SupportingFullArticlePayload]


class StrategistInputPayload(TypedDict):
    company: CompanyScopePayload
    peer_groups: dict[str, Any]
    filters: PayloadFilters
    opportunist_rollup: dict[str, Any]
    views: StrategistViewsPayload
    supporting_articles: SupportingArticlesPayload


class ManagerInputPayload(StrategistInputPayload):
    market_context: dict[str, Any]
    recent_manager_decision_history: list[dict[str, Any]]


class CompanyLinkedArticlesPayload(TypedDict):
    company: dict[str, Any]
    articles: list[Any]

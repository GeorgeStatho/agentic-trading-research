import { useState } from 'react';

import { fetchPolledJson } from './api';
import { formatDateTime, formatDteBucket, formatPercent, formatText, formatToken, formatTokenList } from './format';
import PageHeader from './pageHeader';
import { usePolledResource } from './useRemoteResource';
import './companyDecisions.css';

type StageDecision = {
  decision: string;
  confidence: string;
  summary?: string;
  thesis?: string[];
  risks?: string[];
  reason?: string;
  target_dte_bucket?: string;
  manager_decision_history_id?: number | null;
  selected_option_id?: string;
  selected_expiration_date?: string;
  selected_strike_price?: number | null;
  selected_option_source?: string;
  entry_gate?: { allowed?: boolean; policy_version?: string; reason_codes?: string[]; evidence?: { citation_count?: number; independent_source_count?: number; minimum_evidence_score?: number | null }; technical?: { trend_state?: string; relative_strength_confirmation?: string; volume_confirmation?: string } };
  market_regime_gate?: { allowed?: boolean; regime_state?: string; policy_version?: string; reason_codes?: string[]; enforcement_mode?: string };
};

type HistoryArticleReference = {
  article_id: number;
  title: string;
  source: string;
  published_at: string;
  article_scope: string;
  evidence_layers: string[];
};

type ManagerHistoryEntry = {
  id: number;
  decision_run_at: string;
  manager_decision: string;
  manager_confidence: string;
  manager_reason: string;
  target_dte_bucket: string;
  selected_option_id: string;
  selected_option_symbol: string;
  selected_expiration_date: string;
  selected_strike_price?: number | null;
  selected_option_source: string;
  trade_executed: boolean;
  trade_execution_order_id: string;
  trade_execution_record_id?: number | null;
  latest_trade_pnl_pct?: number | null;
  latest_trade_pnl_updated_at: string;
  pnl_expires_at: string;
  resolved_outcome_label: string;
  article_references: HistoryArticleReference[];
};

type DecisionDiagnostics = {
  historical_bars?: {
    available?: boolean;
    source?: string;
    feed?: string;
    close_count?: number;
    error?: string;
  };
  strategist_validation?: {
    status?: string;
    error_message?: string;
    input_article_count?: number;
    created_at?: string;
  };
};

type CompanyDecision = {
  symbol: string;
  name: string;
  last_updated_at: string;
  strategist: StageDecision;
  manager: StageDecision;
  diagnostics?: DecisionDiagnostics;
  manager_history: ManagerHistoryEntry[];
};

type CompanyDecisionsPayload = {
  as_of: string;
  last_updated_at: string;
  company_count: number;
  companies: CompanyDecision[];
};

type DecisionFilter = 'all' | 'call' | 'put' | 'neither';

function getCompanyDecisions(): Promise<CompanyDecisionsPayload> {
  return fetchPolledJson<CompanyDecisionsPayload>('/api/company-decisions', 'company decisions');
}

function normalizeDecision(value: string | undefined): string {
  return String(value ?? '').trim().toLowerCase().replace(/\s+/g, '_');
}

function decisionTone(value: string | undefined): string {
  const decision = normalizeDecision(value);
  if (decision === 'call' || decision === 'trade_candidate') {
    return 'positive';
  }
  if (decision === 'put') {
    return 'negative';
  }
  return 'neutral';
}

function renderList(items: string[] | undefined, emptyLabel: string) {
  if (!items || items.length === 0) {
    return <p className="company-decisions__text">{emptyLabel}</p>;
  }

  return (
    <ul className="company-decisions__list">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

function renderArticleReferenceList(items: HistoryArticleReference[] | undefined) {
  if (!items || items.length === 0) {
    return <p className="company-decisions__text">No article references saved.</p>;
  }

  return (
    <ul className="company-decisions__list">
      {items.map((item) => (
        <li key={`${item.article_id}-${item.title}`}>
          <strong>{item.title || `Article ${item.article_id}`}</strong>
          <span className="company-decisions__article-reference-meta">
            {[item.source, item.article_scope, item.published_at ? formatDateTime(item.published_at) : '']
              .filter(Boolean)
              .join(' · ')}
          </span>
        </li>
      ))}
    </ul>
  );
}

function CompanyDecisionsPage() {
  const { payload, error } = usePolledResource(getCompanyDecisions, {
    errorMessage: 'Failed to load company decisions.',
  });
  const [searchValue, setSearchValue] = useState('');
  const [activeDecision, setActiveDecision] = useState<DecisionFilter>('all');

  if (error) {
    return (
      <main className="company-decisions">
        <section className="company-decisions__panel">
          <div className="company-decisions__header">
            <div>
              <p className="company-decisions__eyebrow">Decision Archive</p>
              <h2>Decision history unavailable</h2>
            </div>
          </div>
          <p className="company-decisions__empty">{error}</p>
        </section>
      </main>
    );
  }

  if (!payload) {
    return (
      <main className="company-decisions">
        <section className="company-decisions__panel">
          <div className="company-decisions__header">
            <div>
              <p className="company-decisions__eyebrow">Decision Archive</p>
              <h2>Loading company decisions</h2>
            </div>
          </div>
          <p className="company-decisions__empty">Reading stored strategist and manager outputs.</p>
        </section>
      </main>
    );
  }

  const decisionCounts = payload.companies.reduce(
    (counts, company) => {
      const decision = normalizeDecision(company.manager.decision);
      if (decision === 'call' || decision === 'put' || decision === 'neither') {
        counts[decision] += 1;
      }
      return counts;
    },
    { call: 0, put: 0, neither: 0 },
  );
  const normalizedSearch = searchValue.trim().toLowerCase();
  const visibleCompanies = payload.companies.filter((company) => {
    const searchMatches = !normalizedSearch || [company.symbol, company.name, company.manager.decision, company.strategist.decision]
      .some((value) => String(value ?? '').toLowerCase().includes(normalizedSearch));
    const decisionMatches = activeDecision === 'all' || normalizeDecision(company.manager.decision) === activeDecision;
    return searchMatches && decisionMatches;
  });
  const hasFilters = Boolean(normalizedSearch) || activeDecision !== 'all';

  return (
    <main className="company-decisions">
      <section className="company-decisions__panel">
        <PageHeader
          title="Decision ledger"
          description="The latest strategist and manager decision stored for each company. Read-only; refreshes every minute."
          meta={`${payload.company_count} companies · last run ${formatDateTime(payload.last_updated_at)}`}
        />

        <div className="company-decisions__metrics" aria-label="Stored decision summary">
          <article><span>Stored</span><strong>{payload.company_count}</strong></article>
          <article className="company-decisions__metric company-decisions__metric--positive"><span>Calls</span><strong>{decisionCounts.call}</strong></article>
          <article className="company-decisions__metric company-decisions__metric--negative"><span>Puts</span><strong>{decisionCounts.put}</strong></article>
          <article><span>No trade</span><strong>{decisionCounts.neither}</strong></article>
        </div>

        <section className="company-decisions__controls" aria-label="Decision archive filters">
          <label className="company-decisions__search">
            <span>Search decisions</span>
            <input
              type="search"
              value={searchValue}
              onChange={(event) => setSearchValue(event.target.value)}
              placeholder="Symbol, company, strategist, or manager decision"
            />
          </label>
          <div className="company-decisions__filter-group" aria-label="Manager decision filter">
            {(['all', 'call', 'put', 'neither'] as const).map((decision) => (
              <button
                key={decision}
                type="button"
                className={`company-decisions__filter${activeDecision === decision ? ' company-decisions__filter--active' : ''}`}
                onClick={() => setActiveDecision(decision)}
                aria-pressed={activeDecision === decision}
              >
                {decision === 'all' ? 'All decisions' : decision === 'neither' ? 'No trade' : decision}
              </button>
            ))}
            <button
              type="button"
              className="company-decisions__filter company-decisions__filter--reset"
              disabled={!hasFilters}
              onClick={() => {
                setSearchValue('');
                setActiveDecision('all');
              }}
            >
              Reset
            </button>
          </div>
        </section>

        {payload.company_count === 0 ? (
          <p className="company-decisions__empty">No saved strategist or manager decisions were found yet.</p>
        ) : visibleCompanies.length === 0 ? (
          <p className="company-decisions__empty">No stored decisions match the current filters.</p>
        ) : (
          <div className="company-decisions__grid">
            {visibleCompanies.map((company) => (
              <article key={company.symbol} className="company-decisions__card">
                <div className="company-decisions__company">
                  <div>
                    <p className="company-decisions__symbol">{company.symbol}</p>
                    <p className="company-decisions__name">{formatText(company.name)}</p>
                    <div className="company-decisions__decision-summary">
                      <span className={`company-decisions__decision-badge company-decisions__decision-badge--${decisionTone(company.strategist.decision)}`}>
                        Strategist · {formatToken(company.strategist.decision)}
                      </span>
                      <span className={`company-decisions__decision-badge company-decisions__decision-badge--${decisionTone(company.manager.decision)}`}>
                        Manager · {formatToken(company.manager.decision)}
                      </span>
                    </div>
                  </div>
                  <p className="company-decisions__updated">
                    Updated {formatDateTime(company.last_updated_at)}
                  </p>
                </div>

                <div className="company-decisions__stage-grid">
                  <section className="company-decisions__stage">
                    <p className="company-decisions__stage-label">Strategist</p>
                    <p className="company-decisions__stage-main">
                      {formatToken(company.strategist.decision)}
                    </p>
                    <p className="company-decisions__detail">Confidence: {formatText(company.strategist.confidence)}</p>
                    <p className="company-decisions__text">
                      {formatText(company.strategist.summary)}
                    </p>
                    {company.diagnostics?.strategist_validation?.status === 'failed' ? (
                      <div className="company-decisions__diagnostic company-decisions__diagnostic--error">
                        <p className="company-decisions__subheading">Latest strategist attempt failed</p>
                        <p className="company-decisions__text">{formatText(company.diagnostics.strategist_validation.error_message)}</p>
                        <p className="company-decisions__detail">
                          {company.diagnostics.strategist_validation.input_article_count ?? 0} grounded article(s) supplied · {formatDateTime(company.diagnostics.strategist_validation.created_at ?? '')}
                        </p>
                      </div>
                    ) : null}
                    <div className="company-decisions__subsection">
                      <p className="company-decisions__subheading">Thesis</p>
                      {renderList(company.strategist.thesis, 'No thesis points saved.')}
                    </div>
                    <div className="company-decisions__subsection">
                      <p className="company-decisions__subheading">Market regime gate</p>
                      <dl className="company-decisions__facts">
                        <div><dt>Regime</dt><dd>{formatToken(company.manager.market_regime_gate?.regime_state)}</dd></div>
                        <div><dt>Status</dt><dd>{company.manager.market_regime_gate?.allowed === true ? 'Allowed' : company.manager.market_regime_gate?.allowed === false ? 'Blocked' : 'Not recorded'}</dd></div>
                        <div><dt>Mode</dt><dd>{formatToken(company.manager.market_regime_gate?.enforcement_mode)}</dd></div>
                        <div><dt>Policy</dt><dd>{formatText(company.manager.market_regime_gate?.policy_version)}</dd></div>
                      </dl>
                      {company.manager.market_regime_gate?.reason_codes?.length ? <p className="company-decisions__text">Regime reasons: {formatTokenList(company.manager.market_regime_gate.reason_codes)}</p> : null}
                    </div>
                    <div className="company-decisions__subsection">
                      <p className="company-decisions__subheading">Risks</p>
                      {renderList(company.strategist.risks, 'No risks saved.')}
                    </div>
                  </section>

                  <section className="company-decisions__stage">
                    <p className="company-decisions__stage-label">Manager</p>
                    <p className="company-decisions__stage-main">
                      {formatToken(company.manager.decision)}
                    </p>
                    <p className="company-decisions__detail">Confidence: {formatText(company.manager.confidence)}</p>
                    <p className="company-decisions__text">
                      {formatText(company.manager.reason)}
                    </p>
                    <div className="company-decisions__subsection">
                      <p className="company-decisions__subheading">Evidence continuation gate</p>
                      <dl className="company-decisions__facts">
                        <div><dt>Policy</dt><dd>{formatText(company.manager.entry_gate?.policy_version)}</dd></div>
                        <div><dt>Status</dt><dd>{company.manager.entry_gate?.allowed === true ? 'Allowed' : company.manager.entry_gate?.allowed === false ? 'Blocked' : 'Not recorded'}</dd></div>
                        <div><dt>Evidence</dt><dd>{company.manager.entry_gate?.evidence?.citation_count ?? 'N/A'} citations / {company.manager.entry_gate?.evidence?.independent_source_count ?? 'N/A'} sources</dd></div>
                        <div><dt>Trend</dt><dd>{formatToken(company.manager.entry_gate?.technical?.trend_state)}</dd></div>
                        <div><dt>Relative strength</dt><dd>{formatToken(company.manager.entry_gate?.technical?.relative_strength_confirmation)}</dd></div>
                        <div><dt>Volume</dt><dd>{formatToken(company.manager.entry_gate?.technical?.volume_confirmation)}</dd></div>
                      </dl>
                      {company.manager.entry_gate?.reason_codes?.length ? <p className="company-decisions__text">Blocked by: {formatTokenList(company.manager.entry_gate.reason_codes)}</p> : null}
                    </div>
                    {company.diagnostics?.historical_bars?.error ? (
                      <div className="company-decisions__diagnostic company-decisions__diagnostic--error">
                        <p className="company-decisions__subheading">Historical-bars diagnostic</p>
                        <p className="company-decisions__text">{company.diagnostics.historical_bars.error}</p>
                        <p className="company-decisions__detail">
                          Source: {formatText(company.diagnostics.historical_bars.source)} · Feed: {formatText(company.diagnostics.historical_bars.feed)} · {company.diagnostics.historical_bars.close_count ?? 0} valid closes
                        </p>
                      </div>
                    ) : null}
                    <div className="company-decisions__subsection">
                      <p className="company-decisions__subheading">Contract selection</p>
                      <dl className="company-decisions__facts">
                        <div>
                          <dt>History record</dt>
                          <dd>{formatText(company.manager.manager_decision_history_id)}</dd>
                        </div>
                        <div>
                          <dt>Target DTE</dt>
                          <dd>{formatDteBucket(company.manager.target_dte_bucket)}</dd>
                        </div>
                        <div>
                          <dt>Option contract</dt>
                          <dd>{formatText(company.manager.selected_option_id)}</dd>
                        </div>
                        <div>
                          <dt>Expiration</dt>
                          <dd>{formatText(company.manager.selected_expiration_date)}</dd>
                        </div>
                        <div>
                          <dt>Strike</dt>
                          <dd>{formatText(company.manager.selected_strike_price)}</dd>
                        </div>
                        <div>
                          <dt>Source</dt>
                          <dd>{formatToken(company.manager.selected_option_source)}</dd>
                        </div>
                      </dl>
                    </div>
                    <details className="company-decisions__history-details">
                      <summary>Recent history <span>{company.manager_history.length}</span></summary>
                      {company.manager_history.length === 0 ? (
                        <p className="company-decisions__text">No linked manager history saved yet.</p>
                      ) : (
                        <div className="company-decisions__history-list">
                          {company.manager_history.map((entry) => (
                            <article key={`${company.symbol}-${entry.id}`} className="company-decisions__history-item">
                              <div className="company-decisions__history-header">
                                <p className="company-decisions__history-main">
                                  {formatToken(entry.manager_decision)} / {formatText(entry.manager_confidence)}
                                </p>
                                <p className="company-decisions__history-time">
                                  {formatDateTime(entry.decision_run_at)}
                                </p>
                              </div>
                              <dl className="company-decisions__facts">
                                <div>
                                  <dt>DTE bucket</dt>
                                  <dd>{formatDteBucket(entry.target_dte_bucket)}</dd>
                                </div>
                                <div>
                                  <dt>Trade executed</dt>
                                  <dd>{entry.trade_executed ? 'Yes' : 'No'}</dd>
                                </div>
                                <div>
                                  <dt>Latest P/L</dt>
                                  <dd>{formatPercent(entry.latest_trade_pnl_pct)}</dd>
                                </div>
                                <div>
                                  <dt>Outcome</dt>
                                  <dd>{formatToken(entry.resolved_outcome_label)}</dd>
                                </div>
                                <div>
                                  <dt>Order id</dt>
                                  <dd>{formatText(entry.trade_execution_order_id)}</dd>
                                </div>
                              </dl>
                              <p className="company-decisions__text">
                                {formatText(entry.manager_reason)}
                              </p>
                              <div className="company-decisions__subsection">
                                <p className="company-decisions__subheading">Articles used</p>
                                {renderArticleReferenceList(entry.article_references)}
                              </div>
                            </article>
                          ))}
                        </div>
                      )}
                    </details>
                  </section>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

export default CompanyDecisionsPage;

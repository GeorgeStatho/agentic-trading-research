import { useEffect, useState } from 'react';
import './companyDecisions.css';

type StageDecision = {
  decision: string;
  confidence: string;
  summary?: string;
  thesis?: string[];
  risks?: string[];
  reason?: string;
  target_dte_bucket?: string;
  selected_option_id?: string;
  selected_expiration_date?: string;
  selected_strike_price?: number | null;
  selected_option_source?: string;
};

type CompanyDecision = {
  symbol: string;
  name: string;
  last_updated_at: string;
  strategist: StageDecision;
  manager: StageDecision;
};

type CompanyDecisionsPayload = {
  as_of: string;
  last_updated_at: string;
  company_count: number;
  companies: CompanyDecision[];
};

const COMPANY_DECISIONS_POLL_INTERVAL_MS = 60_000;

async function getCompanyDecisions(): Promise<CompanyDecisionsPayload> {
  const response = await fetch(`/api/company-decisions?ts=${Date.now()}`);

  if (!response.ok) {
    throw new Error(`Failed to load company decisions: ${response.status}`);
  }

  return response.json() as Promise<CompanyDecisionsPayload>;
}

function formatValue(value: string | number | null | undefined): string {
  if (value === null || value === undefined) {
    return 'N/A';
  }

  const normalized = String(value).trim();
  return normalized || 'N/A';
}

function formatDateTime(value: string): string {
  const normalized = value.trim();
  if (!normalized) {
    return 'N/A';
  }

  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.getTime())) {
    return normalized;
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed);
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

function CompanyDecisionsPage() {
  const [payload, setPayload] = useState<CompanyDecisionsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    const load = () => {
      getCompanyDecisions()
        .then((nextPayload) => {
          if (!isMounted) {
            return;
          }
          setPayload(nextPayload);
          setError(null);
        })
        .catch((err: unknown) => {
          if (!isMounted) {
            return;
          }
          setError(err instanceof Error ? err.message : 'Failed to load company decisions.');
        });
    };

    load();
    const intervalId = window.setInterval(load, COMPANY_DECISIONS_POLL_INTERVAL_MS);

    return () => {
      isMounted = false;
      window.clearInterval(intervalId);
    };
  }, []);

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

  return (
    <main className="company-decisions">
      <section className="company-decisions__panel">
        <div className="company-decisions__header">
          <div>
            <p className="company-decisions__eyebrow">Decision Archive</p>
            <h2>Stored company decisions</h2>
          </div>
          <div className="company-decisions__meta">
            <span>{payload.company_count} companies</span>
            <span>Last run: {formatDateTime(payload.last_updated_at)}</span>
          </div>
        </div>

        {payload.company_count === 0 ? (
          <p className="company-decisions__empty">No saved strategist or manager decisions were found yet.</p>
        ) : (
          <div className="company-decisions__grid">
            {payload.companies.map((company) => (
              <article key={company.symbol} className="company-decisions__card">
                <div className="company-decisions__company">
                  <div>
                    <p className="company-decisions__symbol">{company.symbol}</p>
                    <p className="company-decisions__name">{formatValue(company.name)}</p>
                  </div>
                  <p className="company-decisions__updated">
                    Updated {formatDateTime(company.last_updated_at)}
                  </p>
                </div>

                <div className="company-decisions__stage-grid">
                  <section className="company-decisions__stage">
                    <p className="company-decisions__stage-label">Strategist</p>
                    <p className="company-decisions__stage-main">
                      {formatValue(company.strategist.decision)}
                    </p>
                    <p className="company-decisions__detail">
                      Confidence: {formatValue(company.strategist.confidence)}
                    </p>
                    <p className="company-decisions__text">
                      {formatValue(company.strategist.summary)}
                    </p>
                    <div className="company-decisions__subsection">
                      <p className="company-decisions__subheading">Thesis</p>
                      {renderList(company.strategist.thesis, 'No thesis points saved.')}
                    </div>
                    <div className="company-decisions__subsection">
                      <p className="company-decisions__subheading">Risks</p>
                      {renderList(company.strategist.risks, 'No risks saved.')}
                    </div>
                  </section>

                  <section className="company-decisions__stage">
                    <p className="company-decisions__stage-label">Manager</p>
                    <p className="company-decisions__stage-main">
                      {formatValue(company.manager.decision)}
                    </p>
                    <p className="company-decisions__detail">
                      Confidence: {formatValue(company.manager.confidence)}
                    </p>
                    <p className="company-decisions__text">
                      {formatValue(company.manager.reason)}
                    </p>
                    <div className="company-decisions__subsection">
                      <p className="company-decisions__subheading">Contract selection</p>
                      <dl className="company-decisions__facts">
                        <div>
                          <dt>Target DTE bucket</dt>
                          <dd>{formatValue(company.manager.target_dte_bucket)}</dd>
                        </div>
                        <div>
                          <dt>Option contract</dt>
                          <dd>{formatValue(company.manager.selected_option_id)}</dd>
                        </div>
                        <div>
                          <dt>Expiration</dt>
                          <dd>{formatValue(company.manager.selected_expiration_date)}</dd>
                        </div>
                        <div>
                          <dt>Strike</dt>
                          <dd>{formatValue(company.manager.selected_strike_price)}</dd>
                        </div>
                        <div>
                          <dt>Source</dt>
                          <dd>{formatValue(company.manager.selected_option_source)}</dd>
                        </div>
                      </dl>
                    </div>
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

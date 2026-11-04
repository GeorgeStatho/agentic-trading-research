import type { ReactNode } from 'react';

import { fetchPolledJson } from './api';
import { formatCurrency, formatDateTime, formatSignedPercent, formatToken, hasNumericValue } from './format';
import { Panel } from './ui/Panel';
import { Tag } from './ui/Tag';
import { TickerChip } from './ui/TickerChip';
import { usePolledResource } from './useRemoteResource';

type StoppedAt = 'strategist' | 'manager' | 'safeguard';

/**
 * `/api/why-bot-traded`. The optional fields are not returned yet
 * (REDESIGN.md section 5, "Latest decision"); each part of the panel that
 * needs one stays hidden until the API sends it.
 */
export type WhyBotTradedPayload = {
  ran_at: string;
  has_decision: boolean;
  ticker: string;
  decision: string;
  confidence: string;
  reason: string;
  selected_contract: string;
  rejected_because: string;
  submitted: boolean;
  company_name?: string;
  strategist_decision?: string;
  stopped_at?: StoppedAt | null;
  contract?: { symbol: string; type: string; expiration_date: string; dte: number; strike: number };
  quantity?: number;
  fill_price?: number | null;
  exit_plan?: { take_profit_pct: number; stop_loss_pct: number; dte_bucket: string };
  factors_for?: string[];
  factors_against?: string[];
  last_traded_contract?: string;
};

const STOPPED_AT_TEXT: Record<StoppedAt, string> = {
  strategist: 'Stopped at the strategist',
  manager: 'Stopped at the manager',
  safeguard: 'Stopped by a safeguard',
};

function getWhyBotTraded(): Promise<WhyBotTradedPayload> {
  return fetchPolledJson<WhyBotTradedPayload>('/api/why-bot-traded', 'trade explanation');
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] text-dim">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function Factors({ label, items, tone }: { label: string; items: string[] | undefined; tone: 'gain' | 'loss' }) {
  if (!items?.length) {
    return null;
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="w-14 text-[11px] text-dim">{label}</span>
      {items.map((item) => <Tag key={item} tone={tone}>{item}</Tag>)}
    </div>
  );
}

function SelectedContract({ payload }: { payload: WhyBotTradedPayload }) {
  const { contract, exit_plan: exitPlan } = payload;

  if (!payload.selected_contract) {
    const why = payload.rejected_because || (payload.stopped_at ? STOPPED_AT_TEXT[payload.stopped_at] : '');
    return (
      <div role="group" aria-label="Selected contract" className="flex flex-col gap-1">
        <div className="text-[11px] text-dim">Selected contract</div>
        <div className="text-[17px] font-semibold">None selected</div>
        {why ? <p className="text-ink-2">{why}</p> : null}
        {payload.last_traded_contract ? (
          <div className="mt-3">
            <div className="text-[11px] text-dim">Last contract traded</div>
            <div className="font-mono text-xs">{payload.last_traded_contract}</div>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div role="group" aria-label="Selected contract" className="flex flex-col gap-3">
      <div>
        <div className="text-[11px] text-dim">Selected contract</div>
        <div className="font-mono text-[17px] font-semibold">{payload.selected_contract}</div>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
        {contract ? (
          <>
            <Detail label="Type">{formatToken(contract.type)}</Detail>
            <Detail label="Expiry · DTE">{contract.expiration_date} · {contract.dte} DTE</Detail>
          </>
        ) : null}
        {payload.quantity !== undefined ? <Detail label="Qty">{payload.quantity}</Detail> : null}
        {hasNumericValue(payload.fill_price) ? <Detail label="Fill">{formatCurrency(payload.fill_price)}</Detail> : null}
        {exitPlan ? (
          <>
            <Detail label="Take profit">
              <span className="text-gain-text">{formatSignedPercent(exitPlan.take_profit_pct, { digits: 0 })}</span>
            </Detail>
            <Detail label="Stop loss">
              <span className="text-loss-text">{formatSignedPercent(exitPlan.stop_loss_pct, { digits: 0 })}</span>
            </Detail>
          </>
        ) : null}
      </dl>
    </div>
  );
}

/** The most important panel on Live: what the bot last decided, and why, in the manager's own words. */
export function LatestDecisionView({ payload }: { payload: WhyBotTradedPayload }) {
  const actions = (
    <>
      {payload.submitted ? <Tag tone="gain">Submitted</Tag> : <Tag>Not submitted</Tag>}
      <a href="#/decisions/decisions" className="text-[11px]">Open in Decisions</a>
    </>
  );

  return (
    <Panel title="Latest decision" meta={formatDateTime(payload.ran_at, { placeholder: '' }) || undefined} actions={actions} flush>
      {!payload.has_decision ? <p className="p-3.5 text-ink-2">{payload.reason}</p> : (
        <div className="flex flex-wrap">
          <div className="flex min-w-0 flex-[2_1_480px] flex-col gap-3 p-3.5">
            <div className="flex flex-wrap items-center gap-2">
              <TickerChip symbol={payload.ticker} />
              {payload.company_name ? <span className="text-ink-2">{payload.company_name}</span> : null}
              <span className="flex items-center gap-1.5 text-[11px] text-dim">
                {payload.strategist_decision ? (
                  <>
                    <span>Strategist</span>
                    <Tag>{formatToken(payload.strategist_decision)}</Tag>
                    <span aria-hidden="true">→</span>
                  </>
                ) : null}
                <span>Manager</span>
                <Tag>{payload.decision}</Tag>
              </span>
              <span className="text-[11px] text-dim">{payload.confidence} confidence</span>
            </div>
            <p className="max-w-[80ch] text-[15px] leading-relaxed">{payload.reason}</p>
            <Factors label="For" items={payload.factors_for} tone="gain" />
            <Factors label="Against" items={payload.factors_against} tone="loss" />
          </div>
          <div className="flex-[1_1_320px] border-t border-base-300 bg-head p-3.5 sm:border-t-0 sm:border-l">
            <SelectedContract payload={payload} />
          </div>
        </div>
      )}
    </Panel>
  );
}

function WhyBotTradedPanel() {
  const { payload, error } = usePolledResource(getWhyBotTraded, {
    errorMessage: 'Failed to load bot trade explanation.',
  });

  if (!payload) {
    return (
      <Panel title="Latest decision">
        <p className={error ? 'text-error' : 'text-dim'}>{error ?? 'Reading the latest strategist and manager outputs…'}</p>
      </Panel>
    );
  }

  return <LatestDecisionView payload={payload} />;
}

export default WhyBotTradedPanel;

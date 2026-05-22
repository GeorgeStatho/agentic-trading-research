import { useEffect, useState } from 'react';
import './status.css';

type ScriptState = 'starting' | 'running' | 'paused' | 'error';

type ScriptStatusPayload = {
  state: ScriptState;
  message: string;
  pid: number;
  updated_at: string;
  sleep_seconds?: number;
  stage?: string;
  current_symbol?: string;
  current_company_name?: string;
  current_sector?: string;
  current_industry?: string;
};

const POLL_INTERVAL_MS = 5_000;
const DOWN_THRESHOLD_MS = 90_000;

function formatUpdatedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return 'unknown';
  }

  return date.toLocaleTimeString();
}

function formatStage(value?: string): string | null {
  const normalized = String(value ?? '').trim();
  if (!normalized) {
    return null;
  }
  return normalized
    .split('_')
    .filter(Boolean)
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(' ');
}

function buildTargetSummary(status: ScriptStatusPayload): string | null {
  const parts: string[] = [];
  if (status.current_symbol) {
    if (status.current_company_name) {
      parts.push(`${status.current_symbol} (${status.current_company_name})`);
    } else {
      parts.push(status.current_symbol);
    }
  } else if (status.current_company_name) {
    parts.push(status.current_company_name);
  }
  if (status.current_sector) {
    parts.push(`Sector: ${status.current_sector}`);
  }
  if (status.current_industry) {
    parts.push(`Industry: ${status.current_industry}`);
  }
  return parts.length > 0 ? parts.join(' • ') : null;
}

function ScriptStatusIndicator() {
  const [status, setStatus] = useState<ScriptStatusPayload | null>(null);
  const [isDown, setIsDown] = useState(false);

  useEffect(() => {
    let isMounted = true;

    const loadStatus = async () => {
      try {
        const response = await fetch(`/api/script-status?ts=${Date.now()}`);
        if (!response.ok) {
          throw new Error(`Failed to load script status: ${response.status}`);
        }

        const payload = (await response.json()) as ScriptStatusPayload;
        if (!isMounted) {
          return;
        }

        const updatedAtMs = new Date(payload.updated_at).getTime();
        const isStale =
          Number.isNaN(updatedAtMs) || Date.now() - updatedAtMs > DOWN_THRESHOLD_MS;

        setStatus(payload);
        setIsDown(isStale);
      } catch {
        if (!isMounted) {
          return;
        }

        setStatus(null);
        setIsDown(true);
      }
    };

    loadStatus();
    const intervalId = window.setInterval(loadStatus, POLL_INTERVAL_MS);

    return () => {
      isMounted = false;
      window.clearInterval(intervalId);
    };
  }, []);

  if (isDown) {
    return (
      <section className="script-status script-status--down" aria-live="polite">
        <span className="script-status__dot" />
        <div>
          <p className="script-status__label">Main Script: Down</p>
          <p className="script-status__message">No fresh heartbeat detected.</p>
        </div>
      </section>
    );
  }

  if (!status) {
    return (
      <section className="script-status script-status--loading" aria-live="polite">
        <span className="script-status__dot" />
        <div>
          <p className="script-status__label">Main Script: Loading</p>
          <p className="script-status__message">Checking script state...</p>
        </div>
      </section>
    );
  }

  const stageLabel = formatStage(status.stage);
  const targetSummary = buildTargetSummary(status);

  return (
    <section className={`script-status script-status--${status.state}`} aria-live="polite">
      <span className="script-status__dot" />
      <div>
        <p className="script-status__label">Main Script: {status.state}</p>
        <p className="script-status__message">{status.message}</p>
        {stageLabel ? (
          <p className="script-status__detail">Stage: {stageLabel}</p>
        ) : null}
        {targetSummary ? (
          <p className="script-status__detail">Current target: {targetSummary}</p>
        ) : null}
        <p className="script-status__meta">Last update: {formatUpdatedAt(status.updated_at)}</p>
      </div>
    </section>
  );
}

export default ScriptStatusIndicator;

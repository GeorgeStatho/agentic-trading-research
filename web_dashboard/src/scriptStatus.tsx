import { useEffect, useState } from 'react';
import './status.css';

type ScriptState = 'starting' | 'running' | 'paused' | 'error';

type ScriptStatusPayload = {
  state: ScriptState;
  message: string;
  pid: number;
  updated_at: string;
  sleep_seconds?: number;
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

function ScriptStatusIndicator() {
  const [status, setStatus] = useState<ScriptStatusPayload | null>(null);
  const [isDown, setIsDown] = useState(false);

  useEffect(() => {
    let isMounted = true;

    const loadStatus = async () => {
      try {
        const response = await fetch(`/script_status.json?ts=${Date.now()}`);
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

  return (
    <section className={`script-status script-status--${status.state}`} aria-live="polite">
      <span className="script-status__dot" />
      <div>
        <p className="script-status__label">Main Script: {status.state}</p>
        <p className="script-status__message">
          {status.message} Last update: {formatUpdatedAt(status.updated_at)}
        </p>
      </div>
    </section>
  );
}

export default ScriptStatusIndicator;

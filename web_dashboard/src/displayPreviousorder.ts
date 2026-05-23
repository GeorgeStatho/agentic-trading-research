type TimeframeValue = string | number;

type PortfolioHistoryData = {
  equity: number[];
  timestamp: TimeframeValue[];
  timeframe?: string;
};

export type GraphPoint = {
  x: string;
  y: number;
  rawTimestamp: TimeframeValue;
};

function normalizeTimestamp(value: TimeframeValue): Date | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return new Date(value * 1000);
  }

  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!trimmed) {
      return null;
    }

    const numeric = Number(trimmed);
    if (Number.isFinite(numeric)) {
      return new Date(numeric * 1000);
    }

    const parsed = new Date(trimmed);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed;
    }
  }

  return null;
}

function formatHumanReadableTimestamp(value: TimeframeValue): string {
  const normalized = normalizeTimestamp(value);
  if (normalized === null) {
    return String(value);
  }

  return normalized.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export async function getGraphData(): Promise<GraphPoint[]> {
  const response = await fetch('/api/portfolio-history');

  if (!response.ok) {
    throw new Error(`Failed to load graph data: ${response.status}`);
  }

  const data = (await response.json()) as PortfolioHistoryData;
  return getEquityAndTimeframe(data);
}

export function getEquityAndTimeframe(portfolioData: PortfolioHistoryData): GraphPoint[] {
  if (portfolioData.equity.length !== portfolioData.timestamp.length) {
    throw new Error('Equity and timeframe arrays must have the same number of values.');
  }

  return portfolioData.timestamp.map((time, index) => ({
    x: formatHumanReadableTimestamp(time),
    y: portfolioData.equity[index],
    rawTimestamp: time,
  }));
}

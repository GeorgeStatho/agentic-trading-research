type TimeframeValue = string | number;

type PortfolioHistoryData = {
  equity: number[];
  timestamp: TimeframeValue[];
  timeframe?: string;
};

export type GraphPoint = {
  x: string | number;
  y: number;
};

export async function getGraphData(): Promise<GraphPoint[]> {
  const response = await fetch('/portfolio_history.json');

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
    x: time,
    y: portfolioData.equity[index],
  }));
}

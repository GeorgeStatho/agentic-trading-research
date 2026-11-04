import { fetchPolledJson } from './api';

/** One option order from `/api/executed-trades`. */
export type ExecutedTrade = {
  id: number;
  order_id: string;
  underlying_symbol: string;
  company_name: string | null;
  option_symbol: string;
  decision: string;
  order_side: string | null;
  order_qty: number | null;
  expiration_date: string | null;
  strike_price: number | null;
  order_status: string | null;
  submitted_at: string | null;
  error: string | null;
};

export type ExecutedTradesResponse = {
  trades: ExecutedTrade[];
};

export function getExecutedTrades(): Promise<ExecutedTradesResponse> {
  return fetchPolledJson<ExecutedTradesResponse>('/api/executed-trades', 'executed trades');
}

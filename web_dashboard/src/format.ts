/**
 * Display formatting shared by every dashboard panel.
 *
 * Panels differ only in how many decimals they show and what they print when
 * the API has no value yet, so those are options rather than reasons to
 * reimplement the same null-guard in each file.
 */

export const MISSING_VALUE_PLACEHOLDER = 'N/A';

type NumericValue = number | null | undefined;

type NumericFormatOptions = {
  placeholder?: string;
  digits?: number;
};

type TextFormatOptions = {
  placeholder?: string;
};

/** True when a value can be formatted as a number, rather than a gap in the data. */
export function hasNumericValue(value: NumericValue): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function signPrefix(value: number): string {
  return value > 0 ? '+' : '';
}

export function formatText(
  value: string | number | null | undefined,
  { placeholder = MISSING_VALUE_PLACEHOLDER }: TextFormatOptions = {},
): string {
  if (value === null || value === undefined) {
    return placeholder;
  }

  return String(value).trim() || placeholder;
}

export function formatCurrency(
  value: NumericValue,
  { placeholder = MISSING_VALUE_PLACEHOLDER, digits = 2 }: NumericFormatOptions = {},
): string {
  if (!hasNumericValue(value)) {
    return placeholder;
  }

  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: digits,
  }).format(value);
}

export function formatSignedCurrency(
  value: NumericValue,
  { placeholder = MISSING_VALUE_PLACEHOLDER, digits = 2 }: NumericFormatOptions = {},
): string {
  if (!hasNumericValue(value)) {
    return placeholder;
  }

  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: digits,
    signDisplay: 'always',
  }).format(value);
}

/** Grouped number for quantities and counts, e.g. "1,234.5". */
export function formatNumber(
  value: NumericValue,
  { placeholder = MISSING_VALUE_PLACEHOLDER, digits = 2 }: NumericFormatOptions = {},
): string {
  if (!hasNumericValue(value)) {
    return placeholder;
  }

  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(value);
}

/** Ungrouped fixed-decimal number for scores and ratios, e.g. "1.23". */
export function formatDecimal(
  value: NumericValue,
  { placeholder = MISSING_VALUE_PLACEHOLDER, digits = 2 }: NumericFormatOptions = {},
): string {
  return hasNumericValue(value) ? value.toFixed(digits) : placeholder;
}

export function formatPercent(
  value: NumericValue,
  { placeholder = MISSING_VALUE_PLACEHOLDER, digits = 2 }: NumericFormatOptions = {},
): string {
  return hasNumericValue(value) ? `${value.toFixed(digits)}%` : placeholder;
}

export function formatSignedPercent(
  value: NumericValue,
  {
    placeholder = MISSING_VALUE_PLACEHOLDER,
    digits = 2,
    suffix = '',
  }: NumericFormatOptions & { suffix?: string } = {},
): string {
  if (!hasNumericValue(value)) {
    return placeholder;
  }

  return `${signPrefix(value)}${value.toFixed(digits)}%${suffix}`;
}

/** Renders a 0-1 rate as a whole percentage, e.g. 0.95 -> "95%". */
export function formatRate(
  value: NumericValue,
  { placeholder = MISSING_VALUE_PLACEHOLDER, digits = 0 }: NumericFormatOptions = {},
): string {
  return hasNumericValue(value) ? `${(value * 100).toFixed(digits)}%` : placeholder;
}

function parseTimestamp(value: string | null | undefined): Date | null {
  const parsed = new Date(String(value ?? '').trim());
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function formatDate(
  value: string | null | undefined,
  { placeholder = MISSING_VALUE_PLACEHOLDER }: TextFormatOptions = {},
): string {
  const parsed = parseTimestamp(value);
  return parsed
    ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(parsed)
    : placeholder;
}

export function formatDateTime(
  value: string | null | undefined,
  { placeholder = MISSING_VALUE_PLACEHOLDER }: TextFormatOptions = {},
): string {
  const parsed = parseTimestamp(value);
  return parsed
    ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(parsed)
    : placeholder;
}

/** Hours and minutes only, e.g. "3:38 PM": for one-line status text. */
export function formatShortTime(
  value: string | null | undefined,
  { placeholder = MISSING_VALUE_PLACEHOLDER }: TextFormatOptions = {},
): string {
  const parsed = parseTimestamp(value);
  return parsed ? new Intl.DateTimeFormat(undefined, { timeStyle: 'short' }).format(parsed) : placeholder;
}

export function formatTimeOfDay(
  value: string | null | undefined,
  { placeholder = MISSING_VALUE_PLACEHOLDER }: TextFormatOptions = {},
): string {
  const parsed = parseTimestamp(value);
  return parsed ? parsed.toLocaleTimeString() : placeholder;
}

/** Turns a snake_case API token into a human-readable label, e.g. "trend_up" -> "trend up". */
/**
 * Readable labels for stored enum values that reach the screen. Anything not
 * listed falls back to underscores-as-spaces, so a new value still reads.
 */
const TOKEN_LABELS: Readonly<Record<string, string>> = {
  do_not_trade: "Don't trade",
  trade_candidate: 'Trade candidate',
  watchlist: 'Watchlist',
  neither: 'No trade',
  call: 'Call',
  put: 'Put',
  not_applicable: 'Not applicable',
  mixed_positive: 'Mixed, leaning positive',
  mixed_negative: 'Mixed, leaning negative',
  manager_not_directional: 'Manager not directional',
  insufficient_independent_sources: 'Too few independent sources',
  relative_volume_below_minimum: 'Relative volume below minimum',
  volume_not_confirmed: 'Volume not confirmed',
  regime_gate_requires_directional_decision: 'Regime gate needs a call or put',
  premium_per_trade_cap: 'Premium cap hit',
  portfolio_total_entry_notional_limit: 'Portfolio notional limit hit',
  take_profit_exit: 'Take-profit exit',
  take_profit: 'Take profit',
  stop_loss: 'Stop loss',
  trailing_profit_stop: 'Trailing profit stop',
  no_fill: 'No fill',
  simulated_closed: 'Simulated close',
  portfolio_rejected: 'Rejected by portfolio limits',
};

export function formatToken(
  value: string | null | undefined,
  { placeholder = MISSING_VALUE_PLACEHOLDER }: TextFormatOptions = {},
): string {
  const raw = String(value ?? '').trim();
  return TOKEN_LABELS[raw.toLowerCase()] ?? (raw.replaceAll('_', ' ') || placeholder);
}

/** "30_45" → "30–45 DTE"; "none" or empty → the placeholder. */
export function formatDteBucket(
  value: string | null | undefined,
  { placeholder = MISSING_VALUE_PLACEHOLDER }: TextFormatOptions = {},
): string {
  const match = /^(\d+)_(\d+)$/.exec(String(value ?? '').trim());
  if (match) return `${match[1]}–${match[2]} DTE`;
  const raw = String(value ?? '').trim();
  return !raw || raw.toLowerCase() === 'none' ? placeholder : formatToken(raw);
}

/** Maps each item of a list of stored values (e.g. gate or warning codes) to its label. */
export function formatTokenList(values: readonly string[] | null | undefined, separator = ', '): string {
  return (values ?? []).filter(Boolean).map((value) => formatToken(value)).join(separator);
}

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

export function formatTimeOfDay(
  value: string | null | undefined,
  { placeholder = MISSING_VALUE_PLACEHOLDER }: TextFormatOptions = {},
): string {
  const parsed = parseTimestamp(value);
  return parsed ? parsed.toLocaleTimeString() : placeholder;
}

/** Turns a snake_case API token into a human-readable label, e.g. "trend_up" -> "trend up". */
export function formatToken(
  value: string | null | undefined,
  { placeholder = MISSING_VALUE_PLACEHOLDER }: TextFormatOptions = {},
): string {
  const normalized = String(value ?? '').trim().replaceAll('_', ' ');
  return normalized || placeholder;
}

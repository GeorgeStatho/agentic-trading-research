/**
 * Shared JSON reads against the dashboard's Flask API.
 *
 * Every panel needs the same three things from a GET: a cache-busting stamp so
 * polled reads are not served stale, a status check, and an error message that
 * names the resource that failed.
 */

/**
 * Fetches JSON and throws when the API reports a non-2xx status.
 *
 * `resourceLabel` completes the sentence "Failed to load ...", so phrase it as
 * a noun, e.g. "open positions".
 */
export async function fetchJson<T>(path: string, resourceLabel: string): Promise<T> {
  const response = await fetch(path);

  if (!response.ok) {
    throw new Error(`Failed to load ${resourceLabel}: ${response.status}`);
  }

  return (await response.json()) as T;
}

/** Same as `fetchJson`, with a cache-busting stamp for repeatedly polled reads. */
export function fetchPolledJson<T>(path: string, resourceLabel: string): Promise<T> {
  const separator = path.includes('?') ? '&' : '?';
  return fetchJson<T>(`${path}${separator}ts=${Date.now()}`, resourceLabel);
}

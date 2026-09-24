import { useEffect, useState } from 'react';

/**
 * Loading state for a panel backed by one API resource.
 *
 * Panels either read a resource once or re-read it on a timer; both need the
 * same cancel-on-unmount handling so a slow response cannot set state after
 * the panel is gone.
 */

export const DEFAULT_POLL_INTERVAL_MS = 60_000;

export type RemoteResource<T> = {
  payload: T | null;
  error: string | null;
  isLoading: boolean;
};

type ResourceOptions = {
  /** Shown when the failure carries no message of its own. */
  errorMessage: string;
};

type PolledResourceOptions = ResourceOptions & {
  intervalMs?: number;
};

/**
 * `load` must be referentially stable (a module-level function, or one chosen
 * from a fixed set); the resource is re-read whenever its identity changes.
 */
function useRemoteResource<T>(
  load: () => Promise<T>,
  errorMessage: string,
  intervalMs: number | null,
): RemoteResource<T> {
  const [payload, setPayload] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;

    const read = () => {
      load()
        .then((nextPayload) => {
          if (isMounted) {
            setPayload(nextPayload);
            setError(null);
          }
        })
        .catch((reason: unknown) => {
          if (isMounted) {
            setError(reason instanceof Error ? reason.message : errorMessage);
          }
        })
        .finally(() => {
          if (isMounted) {
            setIsLoading(false);
          }
        });
    };

    read();

    if (intervalMs === null) {
      return () => {
        isMounted = false;
      };
    }

    const intervalId = window.setInterval(read, intervalMs);

    return () => {
      isMounted = false;
      window.clearInterval(intervalId);
    };
  }, [load, errorMessage, intervalMs]);

  return { payload, error, isLoading };
}

/** Reads the resource once, when the panel mounts. */
export function useFetchedResource<T>(
  load: () => Promise<T>,
  { errorMessage }: ResourceOptions,
): RemoteResource<T> {
  return useRemoteResource(load, errorMessage, null);
}

/** Reads the resource on mount, then re-reads it on a timer until unmount. */
export function usePolledResource<T>(
  load: () => Promise<T>,
  { errorMessage, intervalMs = DEFAULT_POLL_INTERVAL_MS }: PolledResourceOptions,
): RemoteResource<T> {
  return useRemoteResource(load, errorMessage, intervalMs);
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api, type SearchParams, type SearchResponse } from '@/lib/api';

export interface SearchState {
  data: SearchResponse | null;
  loading: boolean;
  error: ApiError | null;
}

interface Loaded {
  /** The serialised params these results belong to. */
  key: string | null;
  data: SearchResponse | null;
  error: ApiError | null;
}

/**
 * Runs a search and keeps the previous results visible while the next one
 * loads.
 *
 * Keeping stale data on screen is the deliberate part. Blanking the list on
 * every filter change makes the page flash and costs the user their scroll
 * position; showing the old results dimmed keeps the page stable while the new
 * ones arrive. Search here takes seconds, not milliseconds, so this is the
 * difference between usable and unpleasant.
 *
 * `loading` is **derived**, not stored: it is simply "the results I hold are
 * not the results that were asked for". Storing it would mean a second piece
 * of state that can disagree with the first — the familiar spinner that never
 * stops because one code path forgot to clear it. Here that state is
 * unrepresentable.
 */
export function useSearch(params: SearchParams | null): SearchState & {
  retry: () => void;
} {
  const [loaded, setLoaded] = useState<Loaded>({
    key: null,
    data: null,
    error: null,
  });
  const [attempt, setAttempt] = useState(0);

  // Tracks the in-flight request so a superseded one cannot overwrite newer
  // results. Without this, a slow request for "a" can land after a fast one
  // for "abc" and silently show results for the wrong query.
  const controllerRef = useRef<AbortController | null>(null);

  const active = Boolean(params?.q.trim());
  const key = active && params ? `${attempt}:${JSON.stringify(params)}` : null;

  useEffect(() => {
    if (!params || !active || key === null) return;

    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    api
      .search(params, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setLoaded({ key, data, error: null });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setLoaded((previous) => ({
          key,
          // Previous results survive an error: a failed retry should not
          // destroy results the user was already reading.
          data: previous.data,
          error:
            error instanceof ApiError
              ? error
              : new ApiError(0, { detail: 'Something went wrong.' }),
        }));
      });

    return () => controller.abort();
    // `params` is recreated every render by the caller; `key` is its stable
    // serialisation and changes exactly when a new request is needed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, active]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  if (!active) return { data: null, loading: false, error: null, retry };

  return {
    data: loaded.data,
    error: loaded.key === key ? loaded.error : null,
    loading: loaded.key !== key,
    retry,
  };
}

/**
 * Debounced autocomplete.
 *
 * Debounced at 180 ms and gated at three characters. Both limits exist to send
 * fewer prefixes of what someone is typing to an upstream suggestion service:
 * every keystroke forwarded is another fragment of a query leaving the
 * instance, so the cheapest privacy win here is simply asking less often.
 */
export function useSuggestions(query: string, enabled = true): string[] {
  const [suggestions, setSuggestions] = useState<string[]>([]);

  const trimmed = query.trim();
  // Derived gate rather than clearing state from inside an effect: the effect
  // version renders once with stale suggestions still showing, then again
  // empty, which reads as a flicker under the cursor.
  const active = enabled && trimmed.length >= 3;

  useEffect(() => {
    if (!active) return;

    const controller = new AbortController();
    const timer = setTimeout(() => {
      api
        .suggestions(trimmed, controller.signal)
        .then((next) => {
          if (!controller.signal.aborted) setSuggestions(next);
        })
        .catch(() => setSuggestions([]));
    }, 180);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [trimmed, active]);

  return active ? suggestions : [];
}

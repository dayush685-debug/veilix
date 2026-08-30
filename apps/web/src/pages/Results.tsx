import { useCallback, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Filters } from '@/components/Filters';
import { ResultCard } from '@/components/ResultCard';
import { SearchBar } from '@/components/SearchBar';
import { useSearch } from '@/hooks/useSearch';
import { usePrefs } from '@/hooks/usePrefs';
import type { SafeSearch, SearchCategory, TimeRange } from '@/lib/api';

export function Results() {
  const [params, setParams] = useSearchParams();
  const { prefs } = usePrefs();

  const query = params.get('q') ?? '';
  const category = (params.get('category') ?? 'general') as SearchCategory;
  const page = Math.max(1, Number(params.get('page') ?? '1') || 1);
  const timeRange = (params.get('time_range') as TimeRange | null) ?? undefined;
  const safesearch = (
    params.has('safesearch') ? Number(params.get('safesearch')) : prefs.safesearch
  ) as SafeSearch;

  const { data, loading, error, retry } = useSearch(
    query
      ? {
          q: query,
          category,
          page,
          language: prefs.language,
          safesearch,
          time_range: timeRange,
        }
      : null,
  );

  // The query belongs in the tab title for switching between tabs, but it also
  // lands in browser history, screenshots, and screen shares. Kept generic on
  // purpose; SearXNG's `query_in_title` is disabled for the same reason.
  useEffect(() => {
    document.title = query ? `${query} — Veilix` : 'Veilix — private search';
    return () => {
      document.title = 'Veilix — private search';
    };
  }, [query]);

  const update = useCallback(
    (changes: Record<string, string | undefined>) => {
      const next = new URLSearchParams(params);
      for (const [key, value] of Object.entries(changes)) {
        if (value === undefined || value === '') next.delete(key);
        else next.set(key, value);
      }
      // Any filter change invalidates the page number: staying on page 4 of a
      // different result set shows an arbitrary slice of something new.
      if (!('page' in changes)) next.delete('page');
      setParams(next);
    },
    [params, setParams],
  );

  const isImageGrid = category === 'images';

  return (
    <div className="mx-auto max-w-5xl px-4">
      <div className="sticky top-0 z-10 bg-[var(--surface)] pb-1 pt-4">
        <SearchBar
          initialQuery={query}
          onSearch={(next) => update({ q: next, page: undefined })}
        />
        <div className="mt-3">
          <Filters
            category={category}
            timeRange={timeRange}
            safesearch={safesearch}
            onCategoryChange={(next) => update({ category: next })}
            onTimeRangeChange={(next) => update({ time_range: next })}
            onSafeSearchChange={(next) => update({ safesearch: String(next) })}
          />
        </div>
      </div>

      {/*
        A live region so results are announced to a screen reader when they
        arrive. Without it, a keyboard user submits a search and hears nothing:
        the page has changed but focus has not moved.
        "polite" waits for a pause rather than interrupting mid-word.
      */}
      <div aria-live="polite" aria-atomic="false" className="sr-only">
        {loading && 'Searching'}
        {!loading && data && `${data.count} results for ${data.query}`}
        {!loading && error && `Search failed: ${error.message}`}
      </div>

      {data && <ResultMeta data={data} />}
      {data?.degraded && <DegradedNotice failures={data.failures} />}

      {error && !data && <ErrorPanel error={error} onRetry={retry} />}
      {loading && !data && <ResultSkeleton grid={isImageGrid} />}

      {data && (
        <div
          // Dimmed rather than replaced while the next search loads: keeps the
          // page from flashing and preserves scroll position.
          className={[
            'transition-opacity',
            loading ? 'pointer-events-none opacity-50' : 'opacity-100',
          ].join(' ')}
        >
          {data.answers.length > 0 && <AnswerPanel answers={data.answers} />}

          {data.count === 0 ? (
            <EmptyState query={data.query} degraded={data.degraded} />
          ) : isImageGrid ? (
            <div className="grid grid-cols-2 gap-3 py-6 sm:grid-cols-3 lg:grid-cols-4">
              {data.results.map((result) => (
                <ResultCard key={`${result.url}-${result.title}`} result={result} />
              ))}
            </div>
          ) : (
            <div className="divide-y divide-[var(--border-subtle)] py-2">
              {data.results.map((result) => (
                <ResultCard key={`${result.url}-${result.title}`} result={result} />
              ))}
            </div>
          )}

          {data.suggestions.length > 0 && (
            <RelatedSearches
              suggestions={data.suggestions}
              onSelect={(next) => update({ q: next, page: undefined })}
            />
          )}

          {data.count > 0 && (
            <Pagination
              page={page}
              hasNext={data.count >= 10}
              onChange={(next) => {
                update({ page: String(next) });
                window.scrollTo({ top: 0 });
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

function ResultMeta({ data }: { data: import('@/lib/api').SearchResponse }) {
  return (
    <p className="pt-3 text-xs text-[var(--text-muted)]">
      {/*
        "on this page", never a web-scale total. The upstream reports no total,
        so any big number here would be invented.
      */}
      {data.count} results on this page · {Math.round(data.timing.total_ms)} ms
      {data.timing.cached && ' · from cache'}
      {data.engines_used.length > 0 && ` · ${data.engines_used.length} engines`}
    </p>
  );
}

/**
 * Reports which upstream engines failed.
 *
 * Shown rather than hidden because a result set assembled from three engines
 * while four were blocked is a materially different answer from one where all
 * seven responded — and the user is the one who should decide whether to
 * retry. Hiding it would make Veilix look more reliable than it is.
 */
function DegradedNotice({
  failures,
}: {
  failures: import('@/lib/api').EngineFailure[];
}) {
  return (
    <details className="mt-3 rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-4 py-2.5 text-xs">
      <summary className="cursor-pointer text-[var(--warn-text)]">
        {failures.length} search {failures.length === 1 ? 'engine' : 'engines'}{' '}
        did not respond — results are from fewer sources than usual
      </summary>
      <ul className="mt-2 space-y-1 text-[var(--text-muted)]">
        {failures.map((failure) => (
          <li key={failure.engine}>
            <span className="font-medium text-[var(--text-secondary)]">
              {failure.engine}
            </span>
            {' — '}
            {failure.reason}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[var(--text-muted)]">
        Upstream engines rate-limit and CAPTCHA self-hosted instances. This is
        normal and usually clears on its own.
      </p>
    </details>
  );
}

function AnswerPanel({ answers }: { answers: string[] }) {
  return (
    <aside className="mt-4 rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4">
      <h2 className="mb-1 text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">
        Quick answer
      </h2>
      {answers.map((answer) => (
        <p key={answer} className="text-sm leading-relaxed">
          {answer}
        </p>
      ))}
    </aside>
  );
}

function RelatedSearches({
  suggestions,
  onSelect,
}: {
  suggestions: string[];
  onSelect: (value: string) => void;
}) {
  return (
    <nav aria-label="Related searches" className="border-t border-[var(--border-subtle)] py-5">
      <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">
        Related
      </h2>
      <ul className="flex flex-wrap gap-2">
        {suggestions.map((suggestion) => (
          <li key={suggestion}>
            <button
              type="button"
              onClick={() => onSelect(suggestion)}
              className="rounded-full border border-[var(--border-subtle)] px-3 py-1.5 text-xs transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--surface-sunken)]"
            >
              {suggestion}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function Pagination({
  page,
  hasNext,
  onChange,
}: {
  page: number;
  hasNext: boolean;
  onChange: (page: number) => void;
}) {
  return (
    <nav
      aria-label="Pagination"
      className="flex items-center justify-center gap-2 border-t border-[var(--border-subtle)] py-6"
    >
      <button
        type="button"
        onClick={() => onChange(page - 1)}
        disabled={page <= 1}
        className="rounded-full border border-[var(--border-subtle)] px-4 py-2 text-sm transition-colors hover:bg-[var(--surface-sunken)] disabled:cursor-not-allowed disabled:opacity-40"
      >
        Previous
      </button>
      <span className="px-3 text-sm text-[var(--text-muted)]">Page {page}</span>
      <button
        type="button"
        onClick={() => onChange(page + 1)}
        disabled={!hasNext}
        className="rounded-full border border-[var(--border-subtle)] px-4 py-2 text-sm transition-colors hover:bg-[var(--surface-sunken)] disabled:cursor-not-allowed disabled:opacity-40"
      >
        Next
      </button>
    </nav>
  );
}

function EmptyState({ query, degraded }: { query: string; degraded: boolean }) {
  return (
    <div className="py-16 text-center">
      <p className="text-lg">No results for “{query}”</p>
      <p className="mx-auto mt-2 max-w-md text-sm text-[var(--text-secondary)]">
        {degraded
          ? 'Several engines were unavailable for this search, so there was less to draw on than usual. Trying again shortly may help.'
          : 'Try different or fewer words, or switch category above.'}
      </p>
    </div>
  );
}

function ErrorPanel({
  error,
  onRetry,
}: {
  error: import('@/lib/api').ApiError;
  onRetry: () => void;
}) {
  return (
    <div className="py-16 text-center" role="alert">
      <p className="text-lg">
        {error.status === 429 ? 'Too many searches' : 'Search is unavailable'}
      </p>
      <p className="mx-auto mt-2 max-w-md text-sm text-[var(--text-secondary)]">
        {error.message}
        {error.retryAfter !== null && ` Try again in ${error.retryAfter} seconds.`}
      </p>
      {error.isRetryable && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 rounded-full bg-[var(--accent)] px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--accent-hover)]"
        >
          Try again
        </button>
      )}
      {error.requestId && (
        <p className="mt-4 font-mono text-2xs text-[var(--text-muted)]">
          {/* Quotable in a bug report, and it maps to a server log line that
              contains no query text. */}
          Reference: {error.requestId}
        </p>
      )}
    </div>
  );
}

function ResultSkeleton({ grid }: { grid: boolean }) {
  const items = Array.from({ length: grid ? 8 : 5 }, (_, i) => i);
  return (
    // aria-hidden: the live region already announces "Searching". A screen
    // reader reading eight placeholder blocks would be noise.
    <div aria-hidden="true" className={grid ? 'grid grid-cols-2 gap-3 py-6 sm:grid-cols-3 lg:grid-cols-4' : 'space-y-6 py-6'}>
      {items.map((i) => (
        <div key={i} className="animate-pulse space-y-2">
          <div className={grid ? 'aspect-square rounded-[var(--radius-card)] bg-[var(--surface-sunken)]' : 'h-3 w-32 rounded bg-[var(--surface-sunken)]'} />
          {!grid && (
            <>
              <div className="h-5 w-3/4 rounded bg-[var(--surface-sunken)]" />
              <div className="h-3 w-full rounded bg-[var(--surface-sunken)]" />
              <div className="h-3 w-5/6 rounded bg-[var(--surface-sunken)]" />
            </>
          )}
        </div>
      ))}
    </div>
  );
}

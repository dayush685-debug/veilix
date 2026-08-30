/**
 * Typed client for the Veilix API.
 *
 * Types mirror `apps/api/src/veilix/schemas/search.py`. They are hand-written
 * rather than generated, because the generated output for this small a surface
 * is harder to read than the source of truth it came from — and a contract
 * test (`api.contract.test.ts`) checks them against the live OpenAPI document,
 * which is what actually catches drift.
 */

export type SearchCategory =
  | 'general' | 'news' | 'images' | 'videos' | 'it'
  | 'science' | 'files' | 'map' | 'music' | 'social media';

export type ResultKind =
  | 'web' | 'image' | 'video' | 'torrent' | 'map' | 'paper' | 'package';

export type TimeRange = 'day' | 'week' | 'month' | 'year';
export type SafeSearch = 0 | 1 | 2;

export interface Media {
  /** Always a Veilix proxy path, never a third-party URL. */
  image_url: string | null;
  thumbnail_url: string | null;
  width: number | null;
  height: number | null;
  duration_s: number | null;
  image_format: string | null;
}

export interface SearchResult {
  url: string;
  title: string;
  snippet: string;
  kind: ResultKind;
  domain: string;
  engines: string[];
  score: number;
  published_at: string | null;
  author: string | null;
  media: Media | null;
}

export interface EngineFailure {
  engine: string;
  reason: string;
}

export interface Infobox {
  title: string;
  content: string;
  url: string | null;
  image_url: string | null;
  attributes: [string, string][];
}

export interface SearchTiming {
  total_ms: number;
  upstream_ms: number | null;
  cached: boolean;
}

export interface SearchResponse {
  query: string;
  category: SearchCategory;
  page: number;
  /**
   * Results on this page. Deliberately NOT a web-scale total — the upstream
   * reports none, so inventing one would be a lie the interface tells.
   */
  count: number;
  results: SearchResult[];
  /** True when some engines failed. Results are still usable, from fewer sources. */
  degraded: boolean;
  failures: EngineFailure[];
  engines_used: string[];
  answers: string[];
  suggestions: string[];
  corrections: string[];
  infoboxes: Infobox[];
  timing: SearchTiming;
}

export interface Engine {
  name: string;
  categories: string[];
  enabled: boolean;
  shortcut: string;
  supports_paging: boolean;
  supports_time_range: boolean;
  supports_safesearch: boolean;
}

export interface EnginesResponse {
  count: number;
  enabled_count: number;
  categories: string[];
  engines: Engine[];
}

export interface ComponentHealth {
  name: string;
  healthy: boolean;
  detail: string | null;
}

export interface HealthResponse {
  status: 'ok' | 'degraded';
  version: string;
  environment: string;
  breaker_state: string;
  components: ComponentHealth[];
}

export interface SearchParams {
  q: string;
  category?: SearchCategory;
  page?: number;
  language?: string;
  safesearch?: SafeSearch;
  time_range?: TimeRange | undefined;
}

/**
 * An API error carrying the RFC 9457 problem details.
 *
 * `requestId` is surfaced in the UI so a user reporting a problem can quote
 * something that maps to a server log line — without us having logged their
 * query to make that possible.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly type: string;
  readonly requestId: string | null;
  readonly retryAfter: number | null;

  constructor(status: number, body: Record<string, unknown> | null) {
    const detail = typeof body?.detail === 'string' ? body.detail : undefined;
    super(detail ?? `Request failed with status ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.type = typeof body?.type === 'string' ? body.type : 'about:blank';
    this.requestId = typeof body?.request_id === 'string' ? body.request_id : null;
    this.retryAfter = typeof body?.retry_after === 'number' ? body.retry_after : null;
  }

  /** Whether retrying the same request could plausibly succeed. */
  get isRetryable(): boolean {
    return this.status === 429 || this.status >= 500;
  }

  /** Whether the failure is ours (or upstream's) rather than the caller's. */
  get isServerSide(): boolean {
    return this.status >= 500;
  }
}

const BASE = '/api/v1';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { Accept: 'application/json', ...init?.headers },
      // No credentials: there is no session to send, and requesting them would
      // create the cookie handling this product exists to avoid.
      credentials: 'omit',
    });
  } catch {
    // fetch rejects only on network failure; an offline user should be told
    // that, not shown a generic error.
    throw new ApiError(0, { detail: 'Could not reach Veilix. Check your connection.' });
  }

  if (!response.ok) {
    let body: Record<string, unknown> | null = null;
    try {
      body = (await response.json()) as Record<string, unknown>;
    } catch {
      // A non-JSON error body (a proxy error page, say) is not worth parsing.
    }
    throw new ApiError(response.status, body);
  }

  return (await response.json()) as T;
}

function buildQuery(params: SearchParams): string {
  const query = new URLSearchParams({ q: params.q });
  if (params.category && params.category !== 'general') query.set('category', params.category);
  if (params.page && params.page > 1) query.set('page', String(params.page));
  if (params.language && params.language !== 'auto') query.set('language', params.language);
  if (params.safesearch !== undefined) query.set('safesearch', String(params.safesearch));
  if (params.time_range) query.set('time_range', params.time_range);
  return query.toString();
}

export const api = {
  search(params: SearchParams, signal?: AbortSignal): Promise<SearchResponse> {
    return request<SearchResponse>(`/search?${buildQuery(params)}`, signal ? { signal } : {});
  },

  suggestions(q: string, signal?: AbortSignal): Promise<string[]> {
    return request<{ suggestions: string[] }>(
      `/search/suggestions?q=${encodeURIComponent(q)}`,
      signal ? { signal } : {},
    )
      .then((r) => r.suggestions)
      // Suggestions are decorative. A failure here must never surface as an
      // error in a search box someone is actively typing into.
      .catch(() => []);
  },

  engines(signal?: AbortSignal): Promise<EnginesResponse> {
    return request<EnginesResponse>('/engines', signal ? { signal } : {});
  },

  health(signal?: AbortSignal): Promise<HealthResponse> {
    return request<HealthResponse>('/health', signal ? { signal } : {});
  },
};

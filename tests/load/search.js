/**
 * Veilix load profile.
 *
 *   docker run --rm -i --network veilix_edge \
 *     -e BASE_URL=http://caddy:8080 \
 *     grafana/k6 run - < tests/load/search.js
 *
 * Runs against the edge, so it exercises the whole path a real user takes:
 * Caddy, security headers, the rate limiter, the cache, and SearXNG.
 *
 * ## Two things this profile does deliberately
 *
 * **It authenticates.** Anonymous callers get 60 requests/minute, which any
 * load test trips within seconds — the result would measure the rate limiter,
 * not the system. Using an API key raises the ceiling to 600/min so the
 * numbers describe the search path.
 *
 * **It mixes cold and warm queries.** A pool of repeated terms measures the
 * cache; unique terms measure the upstream path. Testing only unique queries
 * overstates latency for real traffic, and testing only repeats measures
 * Valkey rather than Veilix.
 */

import http from 'k6/http';
import { check, group } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://caddy:8080';
const API_KEY = __ENV.API_KEY || '';

const coldLatency = new Trend('veilix_cold_search_ms', true);
const warmLatency = new Trend('veilix_warm_search_ms', true);
const degradedRate = new Rate('veilix_degraded_responses');
const rateLimited = new Rate('veilix_rate_limited');

// Repeated terms exercise the cache; each VU appends a nonce for cold queries.
const WARM_TERMS = [
  'distributed systems',
  'privacy engineering',
  'rust ownership',
  'postgres index',
  'kubernetes operator',
];

export const options = {
  scenarios: {
    // Ramp rather than a fixed rate: a cold start on a small host would make
    // a constant-arrival-rate scenario report a queue backlog rather than
    // service latency.
    ramp: {
      executor: 'ramping-vus',
      stages: [
        { duration: '20s', target: 5 },
        { duration: '40s', target: 15 },
        { duration: '20s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    // Generous on purpose. Upstream engines take seconds and several are
    // usually CAPTCHA-blocked, so a tight threshold here would measure the
    // open internet rather than this system.
    http_req_failed: ['rate<0.05'],
    veilix_warm_search_ms: ['p(95)<250'],
    veilix_cold_search_ms: ['p(95)<12000'],
    veilix_rate_limited: ['rate<0.10'],
  },
};

function headers() {
  const h = { Accept: 'application/json' };
  if (API_KEY) h['X-API-Key'] = API_KEY;
  return h;
}

export default function () {
  group('warm - repeated query, expected to hit cache', () => {
    const term = WARM_TERMS[__ITER % WARM_TERMS.length];
    const res = http.get(
      `${BASE}/api/v1/search?q=${encodeURIComponent(term)}&category=general`,
      { headers: headers(), tags: { kind: 'warm' } },
    );

    rateLimited.add(res.status === 429);
    if (res.status === 200) {
      warmLatency.add(res.timings.duration);
      const body = res.json();
      degradedRate.add(body.degraded === true);
      check(res, {
        'warm: returns results': () => body.count >= 0,
        // Guards the deliberate omission: the upstream reports no total, so
        // inventing one would be a lie the API tells.
        'warm: no fabricated total': () => body.total_results === undefined,
      });
    }
  });

  group('cold - unique query, goes upstream', () => {
    const nonce = `${__VU}-${__ITER}-${Date.now()}`;
    const res = http.get(
      `${BASE}/api/v1/search?q=veilix%20load%20${nonce}&category=general`,
      { headers: headers(), tags: { kind: 'cold' } },
    );

    rateLimited.add(res.status === 429);
    if (res.status === 200) {
      coldLatency.add(res.timings.duration);
      degradedRate.add(res.json().degraded === true);
    }
  });

  group('operations endpoints stay responsive under load', () => {
    const res = http.get(`${BASE}/api/v1/live`, { tags: { kind: 'health' } });
    // Exempt from rate limiting on purpose: an orchestrator reading a 429 as
    // "unhealthy" would restart a service behaving exactly as designed.
    check(res, { 'liveness is 200 even under load': (r) => r.status === 200 });
  });
}

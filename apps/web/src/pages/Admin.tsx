import { useEffect, useState } from "react";
import { Section } from "@/components/Section";

/**
 * Operations dashboard.
 *
 * What this deliberately cannot show: individual searches, query text,
 * client addresses, per-user anything. Every figure is an aggregate the
 * Prometheus registry already holds, so this page cannot display something the
 * metrics endpoint does not, which is what stops an admin panel becoming a
 * surveillance tool that happens to have a login.
 *
 * Credentials are held in component state for the session only. They are never
 * written to localStorage: a stored admin password survives the tab, and the
 * convenience is not worth it for a page an operator opens occasionally.
 */
interface EngineHealth {
  engine: string;
  failures: number;
  reasons: Record<string, number>;
}

interface Overview {
  environment: string;
  version: string;
  breaker_state: string;
  requests_total: number;
  searches_total: number;
  searches_degraded: number;
  cache_hits: number;
  cache_misses: number;
  cache_hit_ratio: number | null;
  ratelimit_blocked: number;
  engine_failures: EngineHealth[];
  engines_contributing: Record<string, number>;
  cache_enabled: boolean;
  ratelimit_enabled: boolean;
  ratelimit_requests_per_window: number;
  ratelimit_window_s: number;
}

export function Admin() {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [auth, setAuth] = useState<string | null>(null);
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Derived, not stored. `load` runs from an effect, and setting a
  // loading flag synchronously there causes a cascading render - the same
  // pattern avoided in useSearch. It also removes a second piece of state that
  // could disagree with the first and leave a spinner running forever.
  const loading = auth !== null && data === null && error === null;

  // A tick counter drives the refresh. The effect below depends on it, so
  // incrementing it re-runs the fetch without the effect having to call a
  // setter synchronously, the same shape used in useSearch, and the reason
  // both pass the set-state-in-effect rule rather than suppressing it.
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!auth) return;
    const timer = setInterval(() => setTick((n) => n + 1), 15_000);
    return () => clearInterval(timer);
  }, [auth]);

  useEffect(() => {
    if (!auth) return;
    const controller = new AbortController();

    fetch("/api/v1/admin/overview", {
      headers: { Authorization: `Basic ${auth}` },
      credentials: "omit",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (controller.signal.aborted) return;
        if (response.status === 401) {
          setError("Those credentials were rejected.");
          setAuth(null);
          return;
        }
        if (!response.ok) {
          setError(`Request failed (${response.status}).`);
          return;
        }
        setData((await response.json()) as Overview);
        setError(null);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError("Could not reach the API.");
      });

    return () => controller.abort();
  }, [auth, tick]);

  if (!auth || !data) {
    return (
      <div className="mx-auto max-w-sm px-4 py-16">
        <h1 className="text-2xl font-semibold tracking-tight">Operations</h1>
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          Aggregate system health. No user data is available here.
        </p>

        <form
          className="mt-8 space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            setAuth(btoa(`${username}:${password}`));
          }}
        >
          <label className="block">
            <span className="text-sm">Username</span>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              className="mt-1 w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 py-2 text-sm"
            />
          </label>
          <label className="block">
            <span className="text-sm">Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className="mt-1 w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 py-2 text-sm"
            />
          </label>

          {error && (
            <p role="alert" className="text-sm text-[var(--warn-text)]">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={!password || loading}
            className="w-full rounded-full bg-[var(--accent)] px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--accent-hover)] disabled:opacity-40"
          >
            {loading ? "Checking…" : "Sign in"}
          </button>
        </form>
      </div>
    );
  }

  const cacheLookups = data.cache_hits + data.cache_misses;

  return (
    <div className="mx-auto max-w-4xl px-4 py-10">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">Operations</h1>
        <p className="text-xs text-[var(--text-muted)]">
          v{data.version} · {data.environment} · refreshes every 15s
        </p>
      </div>

      <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile
          label="Search backend"
          value={data.breaker_state}
          hint={
            data.breaker_state === "open"
              ? "Shedding load so the backend can recover"
              : "Circuit breaker state"
          }
          tone={data.breaker_state === "closed" ? "ok" : "warn"}
        />
        <Tile
          label="Cache hit ratio"
          // null and 0% mean different things; rendering them identically
          // sends someone debugging a healthy cache.
          value={
            data.cache_hit_ratio === null
              ? "no data"
              : `${(data.cache_hit_ratio * 100).toFixed(1)}%`
          }
          hint={`${cacheLookups.toFixed(0)} lookups`}
        />
        <Tile
          label="Requests"
          value={data.requests_total.toFixed(0)}
          hint="since process start"
        />
        <Tile
          label="Rate-limit blocks"
          value={data.ratelimit_blocked.toFixed(0)}
          hint={
            data.ratelimit_enabled
              ? `${data.ratelimit_requests_per_window}/${data.ratelimit_window_s}s`
              : "limiter disabled"
          }
          tone={data.ratelimit_enabled ? "neutral" : "warn"}
        />
      </div>

      <Section title="Upstream engines">
        {data.engine_failures.length === 0 ? (
          <p className="text-sm text-[var(--text-secondary)]">
            No engine failures recorded yet.
          </p>
        ) : (
          <>
            <ul className="divide-y divide-[var(--border-subtle)] overflow-hidden rounded-[var(--radius-card)] border border-[var(--border-subtle)]">
              {data.engine_failures.slice(0, 12).map((engine) => (
                <li
                  key={engine.engine}
                  className="flex items-center justify-between gap-4 px-4 py-2.5"
                >
                  <span className="text-sm">{engine.engine}</span>
                  <span className="flex items-center gap-3 text-xs text-[var(--text-muted)]">
                    {Object.entries(engine.reasons).map(([reason, count]) => (
                      <span key={reason}>
                        {reason} ×{count}
                      </span>
                    ))}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-[var(--text-muted)]">
              Derived from failures on real queries. An engine that no recent
              search touched has no row here — its health is <em>unknown</em>,
              not good.
            </p>
          </>
        )}
      </Section>

      <Section title="Engines contributing results">
        {Object.keys(data.engines_contributing).length === 0 ? (
          <p className="text-sm text-[var(--text-secondary)]">No data yet.</p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {Object.entries(data.engines_contributing)
              .sort(([, a], [, b]) => b - a)
              .slice(0, 24)
              .map(([engine, count]) => (
                <li
                  key={engine}
                  className="rounded-full border border-[var(--border-subtle)] px-2.5 py-1 text-2xs"
                >
                  {engine}{" "}
                  <span className="text-[var(--text-muted)]">
                    {count.toFixed(0)}
                  </span>
                </li>
              ))}
          </ul>
        )}
      </Section>

      <Section title="What this page cannot show">
        <p className="text-sm leading-relaxed text-[var(--text-secondary)]">
          There is no view of individual searches, query text, or client
          addresses here, and no way to add one without changing what the system
          collects. Every figure above is an aggregate already present in the
          metrics endpoint, and no metric carries a user-derived label — so an
          operator can see that the system is healthy and cannot see what anyone
          searched for.
        </p>
      </Section>

      {error && (
        <p role="alert" className="mt-6 text-sm text-[var(--warn-text)]">
          {error}
        </p>
      )}
    </div>
  );
}

function Tile({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "ok" | "warn" | "neutral";
}) {
  return (
    <div className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4">
      <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">
        {label}
      </p>
      <p
        className={[
          "mt-1 text-xl font-semibold tabular-nums",
          tone === "warn" ? "text-[var(--warn-text)]" : "",
          tone === "ok" ? "text-[var(--accent)]" : "",
        ].join(" ")}
      >
        {value}
      </p>
      {hint && (
        <p className="mt-0.5 text-2xs text-[var(--text-muted)]">{hint}</p>
      )}
    </div>
  );
}

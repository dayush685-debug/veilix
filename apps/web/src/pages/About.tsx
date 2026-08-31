import { Link } from 'react-router-dom';

export function About() {
  return (
    <article className="mx-auto max-w-2xl px-4 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">About Veilix</h1>

      <p className="mt-4 text-sm leading-relaxed text-[var(--text-secondary)]">
        Veilix is a privacy-first meta-search platform. It does not crawl or
        index the web. It forwards your query to many established search
        engines at once, merges what they return, and does it without building
        a profile of the person asking.
      </p>

      <h2 className="mt-8 text-sm font-medium uppercase tracking-wide text-[var(--text-muted)]">
        Why meta-search
      </h2>
      <p className="mt-3 text-sm leading-relaxed text-[var(--text-secondary)]">
        Mainstream search is funded by profiling. Meta-search breaks the link
        between a person and their queries by putting a server in the middle:
        upstream engines see this instance, not you. You also get results from
        several indexes at once rather than one company&rsquo;s view of the web.
      </p>

      <h2 className="mt-8 text-sm font-medium uppercase tracking-wide text-[var(--text-muted)]">
        What it is built on
      </h2>
      <p className="mt-3 text-sm leading-relaxed text-[var(--text-secondary)]">
        The search core is{' '}
        <a
          href="https://github.com/searxng/searxng"
          rel="noreferrer"
          className="text-[var(--accent)] hover:underline"
        >
          SearXNG
        </a>
        , an established open-source meta-search engine, run unmodified.
        Veilix is the platform around it: a typed API, an orchestration layer
        built for upstreams that fail constantly, a rate limiter that does not
        store addresses, and this interface.
      </p>

      <h2 className="mt-8 text-sm font-medium uppercase tracking-wide text-[var(--text-muted)]">
        Results will sometimes be thin
      </h2>
      <p className="mt-3 text-sm leading-relaxed text-[var(--text-secondary)]">
        Large search engines rate-limit and CAPTCHA self-hosted instances, so
        several are usually unavailable at any moment. When that happens the
        results page says so and names them, instead of quietly returning less
        and looking confident about it. It is the honest trade for not being
        the product.
      </p>

      <h2 className="mt-8 text-sm font-medium uppercase tracking-wide text-[var(--text-muted)]">
        Run your own
      </h2>
      <p className="mt-3 text-sm leading-relaxed text-[var(--text-secondary)]">
        Every privacy guarantee here ultimately rests on trusting whoever
        operates this machine — the{' '}
        <Link to="/privacy" className="text-[var(--accent)] hover:underline">
          privacy page
        </Link>{' '}
        spells out exactly what that operator can still see. The way out of
        that trust requirement is to become the operator. The whole system runs
        with one <code className="font-mono text-xs">docker compose up</code>.
      </p>
    </article>
  );
}

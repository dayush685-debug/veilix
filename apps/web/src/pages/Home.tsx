import { useNavigate } from 'react-router-dom';
import { Link } from 'react-router-dom';
import { SearchBar } from '@/components/SearchBar';

export function Home() {
  const navigate = useNavigate();

  return (
    <div className="mx-auto flex max-w-2xl flex-col items-center px-4 pb-20 pt-[12vh]">
      <h1 className="flex items-center gap-3 text-4xl font-semibold tracking-tight">
        <span aria-hidden="true" className="text-[var(--accent)]">
          ◈
        </span>
        Veilix
      </h1>

      <p className="mt-3 text-center text-[var(--text-secondary)]">
        Search the web without being followed around it.
      </p>

      <div className="mt-8 w-full">
        <SearchBar
          size="lg"
          focusOnMount
          onSearch={(query) => {
            // navigate returns a promise in react-router 7; voiding it makes
            // the handler's void return explicit rather than accidental.
            void navigate(`/search?q=${encodeURIComponent(query)}`);
          }}
        />
      </div>

      <p className="mt-4 text-xs text-[var(--text-muted)]">
        Press{' '}
        <kbd className="rounded border border-[var(--border-subtle)] px-1.5 py-0.5 font-mono text-2xs">
          /
        </kbd>{' '}
        to search from anywhere
      </p>

      {/*
        Four claims, each one checkable against the implementation instead of
        marketing copy. Every card links to the page that substantiates it,
        including the limits.
      */}
      <ul className="mt-16 grid w-full gap-3 sm:grid-cols-2">
        <Claim title="No search history">
          Queries are held in memory for the length of a request and written
          nowhere. There is no table to delete them from.
        </Claim>
        <Claim title="No IP addresses stored">
          Rate limiting uses a daily-rotating keyed hash, so the service can
          count a client without being able to remember one.
        </Claim>
        <Claim title="Images never leak your address">
          Thumbnails are fetched by the server, so image hosts see the
          instance rather than you.
        </Claim>
        <Claim title="Aggregated, not indexed">
          Results come from many engines at once. They see the instance and the
          query — not your browser.
        </Claim>
      </ul>

      <p className="mt-8 text-center text-xs text-[var(--text-muted)]">
        This is a self-hosted instance.{' '}
        <Link to="/privacy" className="underline hover:text-[var(--text-primary)]">
          Read what its operator can still technically see
        </Link>{' '}
        — the honest answer is not “nothing”.
      </p>
    </div>
  );
}

function Claim({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <li className="rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4">
      <h2 className="text-sm font-medium">{title}</h2>
      <p className="mt-1 text-xs leading-relaxed text-[var(--text-secondary)]">
        {children}
      </p>
    </li>
  );
}

import { Link } from 'react-router-dom';

export function NotFound() {
  return (
    <div className="mx-auto max-w-2xl px-4 py-24 text-center">
      <h1 className="text-2xl font-semibold tracking-tight">Page not found</h1>
      <p className="mt-3 text-sm text-[var(--text-secondary)]">
        That address does not match anything here.
      </p>
      <Link
        to="/"
        className="mt-6 inline-block rounded-full bg-[var(--accent)] px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--accent-hover)]"
      >
        Back to search
      </Link>
    </div>
  );
}

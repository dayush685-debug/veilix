import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useCallback, useMemo } from 'react';
import { useHotkeys } from '@/hooks/useHotkeys';
import { usePrefs } from '@/hooks/usePrefs';

export function Layout() {
  const navigate = useNavigate();
  const { prefs, setPref } = usePrefs();

  const hotkeys = useMemo(
    () => ({
      // Focusing the search field rather than navigating home keeps the
      // current results on screen while retyping — losing them would punish
      // the shortcut's most common use.
      //
      // But pages like /about and /privacy have no search box, and the footer
      // advertises this shortcut as working "from anywhere". Silently doing
      // nothing there is worse than either behaviour, so fall back to the home
      // page, which has one. Caught by an end-to-end test, not by review.
      '/': () => {
        const input = document.querySelector<HTMLInputElement>('input[type="search"]');
        if (input) {
          input.focus();
          input.select();
        } else {
          void navigate('/');
        }
      },
      s: () => navigate('/settings'),
      g: () => navigate('/'),
      '?': () => navigate('/shortcuts'),
    }),
    [navigate],
  );
  useHotkeys(hotkeys);

  const toggleTheme = useCallback(() => {
    const isDark = document.documentElement.classList.contains('dark');
    setPref('theme', isDark ? 'light' : 'dark');
  }, [setPref]);

  return (
    <div className="flex min-h-dvh flex-col">
      {/* First tab stop on every page: lets a keyboard user reach the results
          without tabbing through the whole header each time. */}
      <a href="#main" className="skip-link">
        Skip to content
      </a>

      <header className="border-b border-[var(--border-subtle)]">
        <div className="mx-auto flex max-w-5xl items-center gap-6 px-4 py-3">
          <NavLink
            to="/"
            className="flex items-center gap-2 text-base font-semibold tracking-tight"
          >
            <span aria-hidden="true" className="text-[var(--accent)]">
              ◈
            </span>
            Veilix
          </NavLink>

          <nav aria-label="Main" className="flex-1">
            <ul className="flex items-center gap-1 text-sm">
              <NavItem to="/privacy">Privacy</NavItem>
              <NavItem to="/status">Status</NavItem>
              <NavItem to="/about">About</NavItem>
            </ul>
          </nav>

          <button
            type="button"
            onClick={toggleTheme}
            className="rounded-full p-2 text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-sunken)] hover:text-[var(--text-primary)]"
            // The label states the action, not the state. "Dark mode" alone
            // leaves a screen-reader user guessing whether it reports or toggles.
            aria-label={
              prefs.theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'
            }
          >
            <ThemeIcon />
          </button>

          <NavLink
            to="/settings"
            className="rounded-full p-2 text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-sunken)] hover:text-[var(--text-primary)]"
            aria-label="Settings"
          >
            <GearIcon />
          </NavLink>
        </div>
      </header>

      {/* tabIndex={-1} so the skip link can move focus here programmatically. */}
      <main id="main" tabIndex={-1} className="flex-1 outline-none">
        <Outlet />
      </main>

      <footer className="border-t border-[var(--border-subtle)] py-6 text-xs text-[var(--text-muted)]">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-6 gap-y-2 px-4">
          <span>No tracking. No profiling. No search history.</span>
          <NavLink to="/privacy" className="hover:text-[var(--text-primary)]">
            What we can and cannot see
          </NavLink>
          <NavLink to="/shortcuts" className="hover:text-[var(--text-primary)]">
            Keyboard shortcuts
          </NavLink>
          <a
            href="/redoc"
            className="hover:text-[var(--text-primary)]"
            rel="noreferrer"
          >
            API
          </a>
        </div>
      </footer>
    </div>
  );
}

function NavItem({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <li>
      <NavLink
        to={to}
        className={({ isActive }) =>
          [
            'rounded-full px-3 py-1.5 transition-colors',
            isActive
              ? 'bg-[var(--surface-sunken)] text-[var(--text-primary)]'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
          ].join(' ')
        }
      >
        {children}
      </NavLink>
    </li>
  );
}

function ThemeIcon() {
  return (
    <svg
      className="size-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg
      className="size-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1" />
    </svg>
  );
}

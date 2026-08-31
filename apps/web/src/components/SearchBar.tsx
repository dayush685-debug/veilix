import { useEffect, useId, useRef, useState } from 'react';
import { useSuggestions } from '@/hooks/useSearch';
import { usePrefs } from '@/hooks/usePrefs';

interface Props {
  initialQuery?: string;
  onSearch: (query: string) => void;
  /** Renamed from autoFocus: jsx-a11y flags that prop name on any component. */
  focusOnMount?: boolean;
  size?: 'lg' | 'md';
}

/**
 * The search input, with an accessible combobox for suggestions.
 *
 * Built to the ARIA 1.2 combobox pattern rather than a div with a list under
 * it: `role="combobox"` plus `aria-activedescendant` is what tells a screen
 * reader that suggestions exist, how many there are, and which one is
 * highlighted. A visually identical component without these attributes is
 * simply invisible to anyone not looking at the screen.
 */
export function SearchBar({
  initialQuery = '',
  onSearch,
  focusOnMount = false,
  size = 'md',
}: Props) {
  const [query, setQuery] = useState(initialQuery);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);

  const { prefs } = usePrefs();
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const optionId = (index: number) => `${listId}-option-${index}`;

  const suggestions = useSuggestions(query, open && query !== initialQuery);

  // Adjusting state during render instead of in an effect, which is React's
  // documented way to reset state when a prop changes. The effect version
  // renders once with the stale value, then again with the new one, visible
  // as a flash of the previous query when navigating between result pages.
  const [lastInitial, setLastInitial] = useState(initialQuery);
  if (initialQuery !== lastInitial) {
    setLastInitial(initialQuery);
    setQuery(initialQuery);
  }

  // Derived, not stored. The highlight only needs to be valid for the current
  // suggestion list, so clamping here removes a second piece of state that
  // could disagree with the list it indexes into.
  const activeIndex = highlighted < suggestions.length ? highlighted : -1;

  useEffect(() => {
    if (focusOnMount) inputRef.current?.focus();
  }, [focusOnMount]);

  function submit(value: string) {
    const trimmed = value.trim();
    if (!trimmed) return;
    setOpen(false);
    setHighlighted(-1);
    inputRef.current?.blur();
    onSearch(trimmed);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || suggestions.length === 0) {
      if (event.key === 'Escape') inputRef.current?.blur();
      return;
    }

    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        setHighlighted((i) => (i + 1) % suggestions.length);
        break;
      case 'ArrowUp':
        event.preventDefault();
        setHighlighted((i) => (i <= 0 ? suggestions.length - 1 : i - 1));
        break;
      case 'Enter':
        // Only intercept Enter when a suggestion is actually highlighted;
        // otherwise the form submits the typed text, which is what someone
        // who ignored the dropdown expects.
        if (activeIndex >= 0) {
          event.preventDefault();
          submit(suggestions[activeIndex] ?? query);
        }
        break;
      case 'Escape':
        event.preventDefault();
        setOpen(false);
        setHighlighted(-1);
        break;
      case 'Tab':
        setOpen(false);
        break;
    }
  }

  const large = size === 'lg';
  const showList = open && suggestions.length > 0;

  return (
    <form
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        submit(query);
      }}
      className="relative w-full"
    >
      <label htmlFor={`${listId}-input`} className="sr-only">
        Search the web
      </label>

      <div
        className={[
          'flex items-center gap-3 rounded-full border bg-[var(--surface-raised)]',
          'transition-colors focus-within:border-[var(--accent)]',
          'border-[var(--border-subtle)] hover:border-[var(--border-strong)]',
          large ? 'px-5 py-4' : 'px-4 py-2.5',
        ].join(' ')}
      >
        <SearchIcon className={large ? 'size-5' : 'size-4'} />

        <input
          id={`${listId}-input`}
          ref={inputRef}
          type="search"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          // Delayed so a click on a suggestion registers before the list
          // unmounts. Closing on blur immediately is the reason so many
          // autocompletes cannot be clicked.
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={onKeyDown}
          placeholder="Search without being tracked"
          className={[
            'flex-1 bg-transparent outline-none placeholder:text-[var(--text-muted)]',
            // The browser's own search-cancel button is styled per-platform
            // and clashes; ours is consistent and keyboard reachable.
            '[&::-webkit-search-cancel-button]:hidden',
            large ? 'text-lg' : 'text-base',
          ].join(' ')}
          role="combobox"
          aria-expanded={showList}
          aria-controls={showList ? listId : undefined}
          aria-activedescendant={
            activeIndex >= 0 ? optionId(activeIndex) : undefined
          }
          aria-autocomplete="list"
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
          // Nothing here should reach a browser or OS-level history sync.
          data-1p-ignore
        />

        {query && (
          <button
            type="button"
            onClick={() => {
              setQuery('');
              inputRef.current?.focus();
            }}
            className="rounded-full p-1 text-[var(--text-muted)] hover:bg-[var(--surface-sunken)] hover:text-[var(--text-primary)]"
            aria-label="Clear search"
          >
            <CloseIcon className="size-4" />
          </button>
        )}

        <button
          type="submit"
          className={[
            'rounded-full bg-[var(--accent)] font-medium text-white transition-colors',
            'hover:bg-[var(--accent-hover)] disabled:opacity-40',
            large ? 'px-5 py-2 text-sm' : 'px-4 py-1.5 text-sm',
          ].join(' ')}
          disabled={!query.trim()}
        >
          Search
        </button>
      </div>

      {showList && (
        <ul
          id={listId}
          role="listbox"
          aria-label="Search suggestions"
          className="absolute z-20 mt-2 w-full overflow-hidden rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] py-1 shadow-lg"
        >
          {suggestions.map((suggestion, index) => (
            <li
              key={suggestion}
              id={optionId(index)}
              role="option"
              aria-selected={index === activeIndex}
            >
              <button
                type="button"
                // onMouseDown, not onClick: mousedown fires before the input's
                // blur, so the selection is not lost to the closing list.
                onMouseDown={(event) => {
                  event.preventDefault();
                  submit(suggestion);
                }}
                onMouseEnter={() => setHighlighted(index)}
                className={[
                  'flex w-full items-center gap-3 px-4 py-2 text-left text-sm',
                  index === activeIndex
                    ? 'bg-[var(--surface-sunken)]'
                    : 'hover:bg-[var(--surface-sunken)]',
                ].join(' ')}
              >
                <SearchIcon className="size-3.5 shrink-0 text-[var(--text-muted)]" />
                <span className="truncate">{suggestion}</span>
              </button>
            </li>
          ))}
          <li className="border-t border-[var(--border-subtle)] px-4 pb-1 pt-2 text-2xs text-[var(--text-muted)]">
            Suggestions are fetched by the server, so your browser never
            contacts the suggestion provider.
          </li>
        </ul>
      )}

      {!prefs.showThumbnails && (
        <p className="sr-only">Thumbnails are disabled in your settings.</p>
      )}
    </form>
  );
}

function SearchIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      // Decorative: the adjacent label already names the control, so
      // announcing "image" here would only add noise.
      aria-hidden="true"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}

function CloseIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

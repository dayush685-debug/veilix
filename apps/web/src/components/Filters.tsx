import type { SafeSearch, SearchCategory, TimeRange } from '@/lib/api';

const CATEGORIES: { value: SearchCategory; label: string }[] = [
  { value: 'general', label: 'Web' },
  { value: 'images', label: 'Images' },
  { value: 'videos', label: 'Videos' },
  { value: 'news', label: 'News' },
  { value: 'it', label: 'Code' },
  { value: 'science', label: 'Science' },
  { value: 'music', label: 'Music' },
  { value: 'map', label: 'Maps' },
  { value: 'files', label: 'Files' },
];

const TIME_RANGES: { value: TimeRange | ''; label: string }[] = [
  { value: '', label: 'Any time' },
  { value: 'day', label: 'Past day' },
  { value: 'week', label: 'Past week' },
  { value: 'month', label: 'Past month' },
  { value: 'year', label: 'Past year' },
];

const SAFE_LABELS: Record<SafeSearch, string> = {
  0: 'Safe search off',
  1: 'Safe search moderate',
  2: 'Safe search strict',
};

interface Props {
  category: SearchCategory;
  timeRange: TimeRange | undefined;
  safesearch: SafeSearch;
  onCategoryChange: (category: SearchCategory) => void;
  onTimeRangeChange: (range: TimeRange | undefined) => void;
  onSafeSearchChange: (level: SafeSearch) => void;
}

export function Filters({
  category,
  timeRange,
  safesearch,
  onCategoryChange,
  onTimeRangeChange,
  onSafeSearchChange,
}: Props) {
  return (
    <div className="border-b border-[var(--border-subtle)]">
      {/*
        Tabs, as a real tablist. Category is the primary axis of a search
        interface, so it gets arrow-key navigation and proper roles rather than
        a row of styled links.
      */}
      <div
        role="tablist"
        aria-label="Result category"
        className="-mb-px flex gap-1 overflow-x-auto"
      >
        {CATEGORIES.map((item) => {
          const selected = item.value === category;
          return (
            <button
              key={item.value}
              role="tab"
              type="button"
              aria-selected={selected}
              // Roving tabindex: the tablist is one tab stop, and arrow keys
              // move within it. Nine separate tab stops would make reaching
              // the results below tedious for a keyboard user.
              tabIndex={selected ? 0 : -1}
              onClick={() => onCategoryChange(item.value)}
              onKeyDown={(event) => {
                const index = CATEGORIES.findIndex((c) => c.value === category);
                let next = -1;
                if (event.key === 'ArrowRight') next = (index + 1) % CATEGORIES.length;
                if (event.key === 'ArrowLeft')
                  next = (index - 1 + CATEGORIES.length) % CATEGORIES.length;
                if (event.key === 'Home') next = 0;
                if (event.key === 'End') next = CATEGORIES.length - 1;
                if (next >= 0) {
                  event.preventDefault();
                  const target = CATEGORIES[next];
                  if (target) onCategoryChange(target.value);
                }
              }}
              className={[
                'whitespace-nowrap border-b-2 px-3 py-2.5 text-sm transition-colors',
                selected
                  ? 'border-[var(--accent)] font-medium text-[var(--text-primary)]'
                  : 'border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
              ].join(' ')}
            >
              {item.label}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-2 py-2.5">
        <SelectFilter
          label="Time range"
          value={timeRange ?? ''}
          options={TIME_RANGES}
          onChange={(value) =>
            onTimeRangeChange(value === '' ? undefined : (value as TimeRange))
          }
        />

        <SelectFilter
          label="Safe search"
          value={String(safesearch)}
          options={[
            { value: '0', label: SAFE_LABELS[0] },
            { value: '1', label: SAFE_LABELS[1] },
            { value: '2', label: SAFE_LABELS[2] },
          ]}
          onChange={(value) => onSafeSearchChange(Number(value) as SafeSearch)}
        />
      </div>
    </div>
  );
}

/**
 * A native `<select>`, deliberately.
 *
 * A custom dropdown would match the visual design more tightly and would need
 * hundreds of lines to reimplement what the platform already gets right:
 * keyboard navigation, type-ahead, screen-reader semantics, and the native
 * picker on mobile. That trade is not worth making for a filter control.
 */
function SelectFilter({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: readonly { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="inline-flex items-center gap-2">
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="cursor-pointer rounded-full border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 py-1.5 text-xs text-[var(--text-secondary)] transition-colors hover:border-[var(--border-strong)]"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

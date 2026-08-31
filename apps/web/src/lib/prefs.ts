/**
 * User preferences, stored locally and never sent anywhere.
 *
 * These live in localStorage on the user's own device. The server never sees
 * them, which is why Veilix can offer persistent settings without accounts: a
 * preference stored server-side would need an identifier to key it by, and
 * that identifier would be exactly the tracking token the product refuses to
 * create.
 *
 * Every access is wrapped, because localStorage throws outright in some
 * privacy modes, and a user with storage disabled is precisely the audience
 * this product is for, so they must get a working site, not a crash.
 */

export type ThemePref = 'light' | 'dark' | 'system';

export interface Prefs {
  theme: ThemePref;
  safesearch: 0 | 1 | 2;
  language: string;
  openInNewTab: boolean;
  /** Show the engine names that contributed each result. */
  showProvenance: boolean;
  /** Load proxied thumbnails. Off means fewer requests, no image previews. */
  showThumbnails: boolean;
}

export const DEFAULT_PREFS: Prefs = {
  theme: 'system',
  // Moderate by default: "off" should be a choice someone makes, not the
  // setting a first-time visitor is handed.
  safesearch: 1,
  language: 'auto',
  openInNewTab: false,
  showProvenance: true,
  showThumbnails: true,
};

const KEY = 'veilix.prefs';

export function loadPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULT_PREFS;
    const parsed = JSON.parse(raw) as Partial<Prefs>;
    // Merged over defaults so a preferences object written by an older build
    // gains new keys instead of leaving them undefined.
    return { ...DEFAULT_PREFS, ...parsed };
  } catch {
    return DEFAULT_PREFS;
  }
}

export function savePrefs(prefs: Prefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(prefs));
  } catch {
    // Storage unavailable or full. Preferences apply to this session only;
    // that is a degraded experience, not a broken one.
  }
}

export function clearPrefs(): void {
  try {
    localStorage.removeItem(KEY);
    localStorage.removeItem('veilix.theme');
  } catch {
    /* nothing to clear */
  }
}

export function applyTheme(theme: ThemePref): void {
  const dark =
    theme === 'dark' ||
    (theme === 'system' &&
      window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.classList.toggle('dark', dark);
  try {
    // Read by the inline script in index.html before first paint, so a
    // returning dark-mode user never gets a white flash.
    localStorage.setItem('veilix.theme', theme === 'system' ? '' : theme);
  } catch {
    /* falls back to the media query */
  }
}

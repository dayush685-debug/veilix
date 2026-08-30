import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from 'react';
import {
  DEFAULT_PREFS, applyTheme, clearPrefs, loadPrefs, savePrefs, type Prefs,
} from '@/lib/prefs';

interface PrefsContext {
  prefs: Prefs;
  setPref: <K extends keyof Prefs>(key: K, value: Prefs[K]) => void;
  reset: () => void;
}

const Context = createContext<PrefsContext | null>(null);

export function PrefsProvider({ children }: { children: ReactNode }) {
  // Lazy initialiser: localStorage is read once on mount rather than on every
  // render, and the read is inside loadPrefs' try/catch.
  const [prefs, setPrefs] = useState<Prefs>(() => loadPrefs());

  useEffect(() => {
    applyTheme(prefs.theme);
  }, [prefs.theme]);

  // Follow the OS while the user has chosen "system", so switching the system
  // theme updates an open tab instead of requiring a reload.
  useEffect(() => {
    if (prefs.theme !== 'system') return;
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => applyTheme('system');
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, [prefs.theme]);

  const setPref = useCallback(<K extends keyof Prefs>(key: K, value: Prefs[K]) => {
    setPrefs((previous) => {
      const next = { ...previous, [key]: value };
      savePrefs(next);
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    clearPrefs();
    setPrefs(DEFAULT_PREFS);
    applyTheme(DEFAULT_PREFS.theme);
  }, []);

  const value = useMemo(() => ({ prefs, setPref, reset }), [prefs, setPref, reset]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function usePrefs(): PrefsContext {
  const context = useContext(Context);
  if (!context) throw new Error('usePrefs must be used within PrefsProvider');
  return context;
}

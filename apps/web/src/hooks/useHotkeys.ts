import { useEffect } from 'react';

type Handlers = Record<string, () => void>;

/**
 * Global keyboard shortcuts.
 *
 * Shortcuts are suppressed while the user is typing in a field, with one
 * exception: Escape, which must always be able to dismiss. Without that guard,
 * pressing "/" to type a slash inside the search box would instead re-trigger
 * the focus shortcut, the classic bug that makes keyboard shortcuts feel
 * hostile rather than fast.
 */
export function useHotkeys(handlers: Handlers): void {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing =
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement ||
        target?.isContentEditable === true;

      if (typing && event.key !== 'Escape') return;
      // Never shadow the browser's or OS's own shortcuts.
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      const handler = handlers[event.key];
      if (handler) {
        event.preventDefault();
        handler();
      }
    }

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [handlers]);
}

import '@testing-library/jest-dom/vitest';

// jsdom implements neither of these, and both are used on first paint:
// matchMedia by the theme system, scrollTo by pagination. Without stubs the
// components crash in tests for reasons unrelated to what is being tested.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
}

window.scrollTo = () => {};

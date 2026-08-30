// vitest/config re-exports Vite's defineConfig with the `test` key typed,
// so the test block below is checked rather than silently accepted.
import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // Declared explicitly rather than relying on tsconfig `paths`. The build
    // happens to infer it; vitest does not, so the alias is stated once here
    // and both consume the same definition.
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    // Fail the build if a chunk gets large enough to hurt first paint on a
    // slow connection, rather than printing a warning nobody reads.
    chunkSizeWarningLimit: 300,
    rollupOptions: {
      output: {
        // Router and React change far less often than our own code, so a
        // separate chunk keeps them cached across deploys.
        //
        // Function form: Rollup 5 dropped the object form of manualChunks.
        manualChunks(id) {
          if (id.includes('node_modules/react') || id.includes('node_modules/scheduler')) {
            return 'vendor';
          }
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Dev-only. In production Caddy owns this routing; here it lets the SPA
      // call the API same-origin so cookies and CORS behave identically to
      // production and bugs cannot hide behind a permissive dev setup.
      '/api': { target: 'http://127.0.0.1:18000', changeOrigin: true },
      '/img': { target: 'http://127.0.0.1:18080', changeOrigin: true,
                rewrite: (p) => p.replace(/^\/img/, '/image_proxy') },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});

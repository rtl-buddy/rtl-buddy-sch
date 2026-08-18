// Vite config for the rtl-buddy-view interactive viewer.
//
// Layout in the browser via viz.js (Graphviz compiled to WASM), so
// the viewer is fully static and distributable as a single HTML
// file (see embed.py — wraps the build output + an inlined
// view.json into one .html). No server runtime; no fetches at
// open time once embedded.
//
// `base: ''` keeps every asset reference relative so the build
// loads correctly whether served from `/`, a path prefix, or
// opened straight off the filesystem after embed.py inlining.
//
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '',
  plugins: [vue()],
  build: {
    outDir: 'dist',
    sourcemap: true,
    // Tripwire for the budget called out in #18: ≤2 MB **gzipped**
    // for the standalone HTML. Rollup measures raw bytes, so the
    // limit is set above the current floor and re-raised whenever a
    // deliberate engine lands — otherwise it warns on every build and
    // stops meaning anything.
    //
    // Floor today: two layout engines, both inlined (see below).
    // viz.js's Graphviz WASM ≈1.6 MB raw / 620 kB gzipped; elkjs
    // (#163 P2) adds ≈1.5 MB raw, landing at ≈3.1 MB raw / 1.07 MB
    // gzipped — comfortably inside the gzipped budget that actually
    // governs the single-file distribution.
    chunkSizeWarningLimit: 3500,
    rollupOptions: {
      output: {
        // Inline dynamic ``import('@viz-js/viz')`` — and the same for
        // ``import('elkjs')`` — into the main bundle for the
        // standalone HTML use case: ``embed.py`` walks ``<script
        // src>`` references and would otherwise miss the
        // runtime-resolved chunk. The dev server is unaffected (it
        // doesn't run rollup), so first-render perception cost stays
        // at "Vue shell → engine" both ways.
        inlineDynamicImports: true,
      },
    },
  },
  test: {
    environment: 'happy-dom',
    globals: true,
    // Vitest auto-discovers ``*.spec.js`` under the project root,
    // which would pull in the Playwright suite under ``e2e/`` and
    // crash with "Playwright Test did not expect test.describe()
    // to be called here". Constrain it to ``tests/``.
    include: ['tests/**/*.spec.js'],
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
  },
})

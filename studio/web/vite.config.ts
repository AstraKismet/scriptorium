import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Where the build lands, and why it is flat.
//
// `protocol_version` is HTTP/1.0 on this server, so every response closes the
// connection, and every response carries `Cache-Control: no-store` with no
// `ETag` and no `Last-Modified`. A content-hashed filename buys exactly nothing
// here — the browser is forbidden to store the file and re-fetches it on every
// load, one connection per file — while a hashed `assets/` subdirectory costs
// something real: `pyproject.toml`'s package-data glob was single-level until
// 2026-08-14 and a wheel built under it shipped a blank page. The glob is
// recursive now, so nesting would ship; flat is still the right answer because
// the only thing more files buys on this transport is more connections.
//
// So: three artifacts, fixed names, no `assets/` directory.
export default defineConfig({
  plugins: [react()],

  // **`mpa`, not the default `spa`.** Vite's dev server answers an unknown path
  // with `index.html`; this project's server answers it with `404 not found` in
  // plain text, deliberately, and the contract argues for that rather than
  // merely happening to do it. Left on the default, path-mode routing would
  // *appear to work* all through development and 404 the day it shipped — the
  // development environment giving the opposite feedback to the truth.
  appType: 'mpa',

  server: {
    proxy: {
      // The dev server and `lx web` are two ports, and two loopback ports are
      // `same-site` rather than `same-origin` — which the admission gate refuses
      // alongside `cross-site`, because a page on another loopback port is not
      // this server. So a naive proxy forwards `Origin: http://localhost:5173`
      // and every POST is a 403.
      //
      // Both headers are **removed** rather than rewritten. "Absent is not the
      // same as wrong" is the gate's own rule, and it is what lets `curl`, an
      // editor plugin and `lx` itself through — so a proxy that sends neither is
      // taking the door those already use, not inventing a new one.
      '/api': {
        target: 'http://127.0.0.1:8787',
        changeOrigin: false,
        configure: (proxy) => {
          proxy.on('proxyReq', (request) => {
            request.removeHeader('origin')
            request.removeHeader('sec-fetch-site')
          })
        },
      },
    },
  },

  build: {
    outDir: '../../src/scriptorium/web/static',
    emptyOutDir: true,
    assetsDir: '.',
    // The workbench is a loopback tool opened in whatever browser the person
    // already has. Anything that runs React 19 runs this.
    target: 'es2022',
    // No preload directives: one entry chunk means there is nothing to preload,
    // and the polyfill is dead weight on a page that ships one script.
    modulePreload: { polyfill: false },
    sourcemap: false,
    rollupOptions: {
      output: {
        entryFileNames: 'workbench.js',
        chunkFileNames: 'workbench-[name].js',
        assetFileNames: 'workbench.[ext]',
      },
    },
  },
})

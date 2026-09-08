import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Separate from `vite.config.ts` on purpose: the build config names an output
// directory inside the Python package, and a test runner has no business
// reading that. Nothing here is shared, so nothing here can drift.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: false,
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    setupFiles: ['./src/test/setup.ts'],
  },
})

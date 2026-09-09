import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'

import * as drafts from '../drafts'
import { install } from './wire'

// jsdom has no `ResizeObserver`, and `virtua` constructs one the moment a list
// mounts. A stub that observes nothing is the right shape here: these tests are
// about what the application *does*, and layout measurement is the one thing
// jsdom could not answer truthfully even with a real implementation — every
// element it lays out is zero by zero.
if (!('ResizeObserver' in globalThis)) {
  class Stub {
    observe(): void { /* jsdom measures nothing */ }
    unobserve(): void { /* as above */ }
    disconnect(): void { /* as above */ }
  }
  Object.defineProperty(globalThis, 'ResizeObserver', { value: Stub, writable: true })
}

// `drafts` is module state on purpose — it is what keeps a keystroke out of the
// store — so it is also state a test leaks into the next one. Cleared here
// rather than in each file, because the one that forgets is the one that
// produces a passing test with somebody else's edit in it.
beforeEach(() => {
  drafts.clear()
  install()
})

// `globals: false` means Testing Library's own automatic cleanup never
// registers, so a component rendered by one test is still in the document for
// the next one — which shows up as "found multiple elements" on a query that is
// perfectly correct.
afterEach(cleanup)

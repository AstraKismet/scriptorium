import { beforeEach } from 'vitest'

import * as drafts from '../drafts'
import { install } from './wire'

// `drafts` is module state on purpose — it is what keeps a keystroke out of the
// store — so it is also state a test leaks into the next one. Cleared here
// rather than in each file, because the one that forgets is the one that
// produces a passing test with somebody else's edit in it.
beforeEach(() => {
  drafts.clear()
  install()
})

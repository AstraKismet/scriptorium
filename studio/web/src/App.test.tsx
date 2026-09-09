/**
 * The application mounts, opens what the address names, and settles.
 *
 * Every other test in this directory drives the store and mounts no component,
 * which leaves the whole route → open → render path unexercised — and that path
 * is where a client's worst failures live, because they are silent: a page that
 * fetches the same document once per render, or one that never fetches it at
 * all, looks the same from the store's side.
 *
 * What jsdom cannot answer is anything about layout, and the ledger is
 * virtualized. See the note on the last test.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { App } from './App'
import { useStore } from './store'
import { callsTo, otherwise, replies } from './test/wire'
import type { DocResponse, StateResponse } from './contract'

const initial = useStore.getState()

const state: StateResponse = {
  contract_version: 4,
  version: '0.4.0',
  cwd: '/books',
  targets: ['zh-TW'],
  providers: [{
    name: 'local', kind: 'openai', model: 'qwen', base_url: 'http://127.0.0.1:8080/v1',
    needs_key: false, key_present: true, key_env: '',
    timeout: null, temperature: null, max_tokens: null, retries: null,
  }],
  routing: { draft: { provider: 'local', model: 'qwen' }, polish: { provider: 'local', model: 'qwen' }, repair: { provider: 'local', model: 'qwen' } },
  docs: [{ source: 'book/ch1.md', lang: 'zh-TW', total: 3, done: 2 }],
  untracked: [],
  collisions: [],
}

const doc: DocResponse = {
  source: 'book/ch1.md',
  lang: 'zh-TW',
  tone: 'literary',
  report: { segments: 3, translated: 2, errors: 1, warnings: 0, by_rule: { missing: 1 } },
  segments: [
    { id: 's0001', kind: 'heading', status: 'translated', origin: 'human', source: 'Chapter 1', target: '第一章', review: null, waived: false, token: 't1', issues: [] },
    { id: 's0002', kind: 'para', status: 'translated', origin: 'llm:draft', source: 'She had not slept.', target: '她沒有睡。', review: 'held', waived: false, token: 't2', issues: [] },
    { id: 's0003', kind: 'para', status: 'pending', origin: null, source: 'The lamp was still burning.', target: '', review: null, waived: false, token: 't3', issues: [{ seg: 's0003', rule: 'missing', severity: 'error', message: 'no translation' }] },
  ],
}

beforeEach(() => {
  useStore.setState(initial, true)
  window.location.hash = ''
})

afterEach(() => { window.location.hash = '' })

describe('the shell', () => {
  it('boots and draws the rail', async () => {
    replies({ body: state })
    otherwise({ body: { provider: 'local', configured: 'qwen', models: [], error: null } })
    render(<App />)
    await waitFor(() => { expect(screen.getByText('book/ch1.md')).toBeTruthy() })
  })

  it('refuses a contract version it does not know', async () => {
    replies({ body: { ...state, contract_version: 99 } })
    render(<App />)
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /does not know this server/ })).toBeTruthy()
    })
    // Both numbers, so a reader knows which half to move.
    expect(screen.getByText(/reports/).textContent).toContain('99')
  })

  /**
   * ⚠️ **Nothing below asserts on a segment row, and that is not an oversight.**
   * The ledger is virtualized, the virtualizer measures with a `ResizeObserver`,
   * and jsdom lays nothing out — so it renders zero rows here whatever the code
   * does. Asserting on a row would either fail forever or, worse, be "fixed" by
   * mocking the measurement, which would leave a test that passes over a list
   * that shows nothing. What is proved here is the part jsdom can answer: the
   * address opens the document, once, and the page settles.
   */
  it('opens the document the address names, and settles', async () => {
    window.location.hash = '#/doc/zh-TW/book%2Fch1.md'
    replies({ body: state }, { body: { provider: 'local', configured: 'qwen', models: [], error: null } })
    otherwise({ body: doc })
    render(<App />)
    // The toolbar's tally is drawn from the document that was fetched.
    await waitFor(() => { expect(screen.getByText('/3 translated')).toBeTruthy() }, { timeout: 4000 })
    // The register the document is frozen in, shown because a reviewer choosing
    // to start over needs it — and never sent back as the value of anything.
    expect(screen.getByText('literary')).toBeTruthy()
    // A loop would show up as a request per render rather than one per document.
    expect(callsTo('/api/doc')).toHaveLength(1)
  })
})

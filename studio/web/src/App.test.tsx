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
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { App } from './App'
import { SegmentRow } from './components/SegmentRow'
import { useStore } from './store'
import { callsTo, otherwise, replies } from './test/wire'
import { CONTRACT_VERSION } from './contract'
import type { DocResponse, StateResponse } from './contract'

const initial = useStore.getState()

// The pin itself, never a copy of its number: a fixture holding `4` was a
// second source of a value `contract.ts` owns, and moved one bump late.
const state: StateResponse = {
  contract_version: CONTRACT_VERSION,
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

/**
 * Moving from one segment to another, which is most of what a reviewer does.
 *
 * HANDOFF-084, measured in a painted Chrome tab against the committed build: the
 * first click on a segment worked and the second blanked the page. The address
 * and the focus were each kept in step with the other by an effect of its own,
 * so once both named a segment and the two differed, each effect wrote its own
 * side over the other in the same commit, the next commit found them swapped,
 * and React stopped the loop with error #185 and unmounted the whole tree. The
 * address was left on the first segment. Any door that makes the two differ
 * reaches it — a click, and an address that names another paragraph — which is
 * why both doors have a test here.
 *
 * **The rows are rendered beside the application rather than inside the
 * ledger**, for the reason the note above gives: the virtualized ledger mounts
 * none in jsdom. They are the same component with the same handlers, bound to
 * the same store and the same address, so a click on one is the click a ledger
 * row receives. Measurement is not mocked to get them into the list — a test
 * that did would pass over a ledger that shows nothing. And the clicks go
 * through `user-event` rather than `fireEvent`, because a click on a textarea is
 * a focus *and* a click, and those are two writers of the same fact.
 */
describe('moving between segments', () => {
  const second: DocResponse = {
    ...doc,
    source: 'book/ch2.md',
    report: { segments: 2, translated: 0, errors: 0, warnings: 0, by_rule: {} },
    segments: [
      { ...doc.segments[2]!, id: 's0001', source: 'Morning came late.', target: '', token: 'u1', issues: [] },
      { ...doc.segments[2]!, id: 's0002', source: 'Nobody spoke.', target: '', token: 'u2', issues: [] },
    ],
  }
  // The router remembers where the address stood in each document for as long
  // as the module lives, which in this file is every test. A document is
  // therefore visited by one test only, so that no test depends on the order
  // they run in.
  const third: DocResponse = { ...second, source: 'book/ch3.md' }
  const bySource = new Map([doc, second, third].map(d => [d.source, d]))

  /**
   * Answer by path. The margin fetches on a timer of its own, so a queue would
   * sooner or later hand a document to a style request and take the margin
   * down for a reason that has nothing to do with navigation.
   */
  const serve = (): void => {
    const project: StateResponse = {
      ...state,
      docs: [
        ...state.docs,
        { source: second.source, lang: 'zh-TW', total: 2, done: 0 },
        { source: third.source, lang: 'zh-TW', total: 2, done: 0 },
      ],
    }
    const answer = (path: string): unknown => {
      if (path.startsWith('/api/state')) return project
      if (path.startsWith('/api/models')) return { provider: 'local', configured: 'qwen', models: [], error: null }
      if (path.startsWith('/api/doc')) {
        return bySource.get(new URLSearchParams(path.split('?')[1]).get('src') ?? '') ?? doc
      }
      if (path.startsWith('/api/preview')) {
        const blocks = doc.segments.map(s => ({ id: s.id, kind: s.kind, from: 'source' as const, text: s.source }))
        return { text: blocks.map(b => b.text).join(''), blocks, missing: 0, default_out: 'out.md' }
      }
      if (path.startsWith('/api/sentences')) return { sentences: [] }
      // `/api/style` and `/api/suggest`, answered empty in one shape both read.
      return { source: '', lang: 'zh-TW', tone: null, ids: [], voice: '', voice_notes: [], algorithm: 'none', cutoff: 0, records: 0, segments: [] }
    }
    globalThis.fetch = ((input: RequestInfo | URL) => Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(answer(String(input))),
    } as Response)) as typeof fetch
  }

  /**
   * Every write the page makes to the address, counted.
   *
   * A navigation writes the address at most once, and the defect this block
   * exists for wrote it 27 times for one click before React gave up. Counting is
   * also what lets these tests *fail* on that defect rather than hang: a real
   * browser bounds a flood of `replaceState` calls — Chrome ignores them past a
   * rate, Firefox and Safari throw — and jsdom bounds nothing, so without the cap
   * below the loop spins forever and no timeout can fire.
   */
  const realReplace = window.history.replaceState
  let writes = 0
  const CAP = 50

  beforeEach(() => {
    writes = 0
    window.history.replaceState = function replaceState(this: History, ...args: Parameters<History['replaceState']>) {
      writes += 1
      if (writes > CAP) throw new Error(`the address was written ${writes} times — two writers are overwriting each other`)
      realReplace.apply(this, args)
    }
  })

  afterEach(() => { window.history.replaceState = realReplace })

  const address = (src: string, seg?: string): string =>
    `#/doc/zh-TW/${encodeURIComponent(src)}` + (seg ? `?seg=${seg}` : '')

  /** The margin names the segment it is about, and it is drawn only while the
   *  shell is — so finding it is also finding that the page is not blank. */
  const marginIsAbout = (id: string): void => {
    expect(screen.getByRole('heading', { name: id })).toBeTruthy()
    expect(screen.getByText('/3 translated')).toBeTruthy()
  }

  const opened = async (): Promise<void> => {
    serve()
    render(<App />)
    await waitFor(() => { expect(screen.getByText('/3 translated')).toBeTruthy() }, { timeout: 4000 })
  }

  it('renders every segment clicked after the first, and moves the address with it', async () => {
    window.location.hash = address(doc.source)
    await opened()
    const rows = render(<>{doc.segments.map(s => <SegmentRow key={s.id} seg={s} />)}</>)
    const row = (id: string): HTMLElement =>
      rows.container.querySelectorAll<HTMLElement>('.row')[doc.segments.findIndex(s => s.id === id)]!
    const user = userEvent.setup()

    // The first click, then at least five more, alternating the two handlers a
    // row has: its source text takes the row's click, its field takes a focus
    // and a click together.
    const steps: Array<[string, 'text' | 'field']> = [
      ['s0001', 'text'], ['s0003', 'text'], ['s0002', 'field'],
      ['s0003', 'field'], ['s0001', 'text'], ['s0002', 'text'], ['s0003', 'text'],
    ]
    for (const [id, part] of steps) {
      const target = part === 'field'
        ? within(row(id)).getByRole('textbox')
        : within(row(id)).getByText(doc.segments.find(s => s.id === id)!.source)
      const before = writes
      await user.click(target)
      await waitFor(() => {
        marginIsAbout(id)
        expect(window.location.hash).toBe(address(doc.source, id))
      })
      expect(writes - before).toBeLessThanOrEqual(1)
    }
  })

  it('follows an address that names another segment — Back, Forward, or a link', async () => {
    window.location.hash = address(doc.source, 's0001')
    await opened()
    await waitFor(() => { marginIsAbout('s0001') })

    const before = writes
    window.location.hash = address(doc.source, 's0003')
    await waitFor(() => {
      marginIsAbout('s0003')
      expect(window.location.hash).toBe(address(doc.source, 's0003'))
    })
    // The person moved the address; nothing should move it back.
    expect(writes - before).toBe(0)
  })

  it('does not carry a segment of one document into the next one opened', async () => {
    window.location.hash = address(doc.source, 's0003')
    await opened()
    await waitFor(() => { marginIsAbout('s0003') })

    // The rail's own link, which addresses the document and no paragraph in it.
    await userEvent.setup().click(screen.getByRole('button', { name: /book\/ch2\.md/ }))
    await waitFor(() => { expect(screen.getByText('/2 translated')).toBeTruthy() }, { timeout: 4000 })

    // Ids restart at `s0001` in every document, so `s0003` here would be a
    // paragraph of this chapter that nobody chose.
    expect(window.location.hash).toBe(address(second.source))
    expect(screen.queryByRole('heading', { name: 's0003' })).toBeNull()
    expect(screen.getByText(/Choose a segment/)).toBeTruthy()
  })

  /*
   * The three below pass on the build that blanked the page, and are here for
   * the repair rather than the defect. The effect that copied the store's focus
   * into the address was also what put a paragraph back on an address that had
   * lost it, and three ordinary trips relied on that. With the segment kept in
   * the address alone, each of them has to carry it — and a version that forgot
   * to lands a five-thousand-segment novel at its top without failing anything
   * above.
   */

  it('comes back from the reading view to the paragraph it left', async () => {
    window.location.hash = address(doc.source, 's0003')
    await opened()
    await waitFor(() => { marginIsAbout('s0003') })

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Read' }))
    await waitFor(() => { expect(window.location.hash).toBe(`#/read/zh-TW/${encodeURIComponent(doc.source)}?seg=s0003`) })
    await user.click(await screen.findByRole('button', { name: /Back to the ledger/ }))
    await waitFor(() => {
      expect(window.location.hash).toBe(address(doc.source, 's0003'))
      marginIsAbout('s0003')
    })
  })

  it('keeps the paragraph when the rail opens the document that is already open', async () => {
    window.location.hash = address(doc.source, 's0002')
    await opened()
    await waitFor(() => { marginIsAbout('s0002') })

    await userEvent.setup().click(screen.getByRole('button', { name: /book\/ch1\.md/ }))
    await waitFor(() => {
      expect(window.location.hash).toBe(address(doc.source, 's0002'))
      marginIsAbout('s0002')
    })
  })

  it('returns to each document at its own paragraph, never the other one\'s', async () => {
    window.location.hash = address(doc.source, 's0003')
    await opened()
    await waitFor(() => { marginIsAbout('s0003') })

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /book\/ch3\.md/ }))
    await waitFor(() => { expect(screen.getByText('/2 translated')).toBeTruthy() }, { timeout: 4000 })
    window.location.hash = address(third.source, 's0001')
    await waitFor(() => { expect(screen.getByRole('heading', { name: 's0001' })).toBeTruthy() })

    await user.click(screen.getByRole('button', { name: /book\/ch1\.md/ }))
    await waitFor(() => {
      expect(window.location.hash).toBe(address(doc.source, 's0003'))
      marginIsAbout('s0003')
    }, { timeout: 4000 })
  })
})

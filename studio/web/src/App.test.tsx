/**
 * The application mounts, opens what the address names, and settles.
 *
 * The other tests in this directory drive the store, or mount one component on
 * its own, which leaves the whole route → open → render path unexercised — and
 * that path is where a client's worst failures live, because they are silent: a
 * page that fetches the same document once per render, or one that never
 * fetches it at all, looks the same from the store's side.
 *
 * What jsdom cannot answer is anything about layout, and the ledger is
 * virtualized. See the note on 'opens the document the address names, and
 * settles', and the one on 'moving between segments'.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import { SegmentRow } from './components/SegmentRow'
import * as drafts from './drafts'
import * as routes from './router'
import { useStore } from './store'
import { answering, calls, callsTo, otherwise, replies } from './test/wire'
import type { Answer } from './test/wire'
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
   * ⚠️ **This test asserts on no ledger row, and that is not an oversight.**
   * The ledger is virtualized, the virtualizer measures with a `ResizeObserver`,
   * and jsdom lays nothing out — so it renders no row for its range here
   * whatever the code does. The one exception is the row the address names,
   * which `keepMounted` renders regardless. Asserting on the list would either
   * fail forever or, worse, be "fixed" by mocking the measurement, which would
   * leave a test that passes over a list that shows nothing. What is proved
   * here is the part jsdom can answer: the address opens the document, once,
   * and the page settles. The block after this one clicks rows, and renders
   * them outside the ledger for exactly this reason.
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
 * none for its range in jsdom. They are the same component with the same
 * handlers, bound to the same store and the same address, so a click on one is
 * the click a ledger row receives — though not the keyboard move to the next
 * row, which looks its target up inside `.ledger`; the focus that move produces
 * is driven directly instead. Measurement is not mocked to get them into the
 * list — a test that did would pass over a ledger that shows nothing. And the
 * clicks go through `user-event` rather than `fireEvent`, because a click on a
 * textarea is a focus *and* a click, and those are two writers of the same fact.
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
  // as the module lives, which in this file is every test. `book/ch2.md` and
  // `book/ch3.md` are each visited by one test only; `book/ch1.md` is opened by
  // every test, so a test that relies on its remembered paragraph sets that
  // paragraph with its own opening address first.
  const third: DocResponse = { ...second, source: 'book/ch3.md' }
  const bySource = new Map([doc, second, third].map(d => [d.source, d]))

  /**
   * Answer by path. The margin fetches on a timer of its own, so a queue would
   * sooner or later hand a document to a style request and take the margin
   * down for a reason that has nothing to do with navigation.
   *
   * It goes through `wire.answering` rather than replacing `globalThis.fetch`,
   * which is what it used to do: a second stub is a second record of what was
   * sent, and `calls` — the vocabulary every other test in this repository
   * speaks — could not see any of it.
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
    answering(call => ({ body: answer(call.path) }))
  }

  /**
   * Every `replaceState` the page makes, counted. `routes.go` assigns
   * `location.hash` and is not counted here — `history.length` is what catches
   * a row that navigates instead of replacing.
   *
   * A move to another segment writes the address once, and the defect this
   * block exists for wrote it 27 times for one click before React gave up. The
   * cap is what lets these tests *fail* on that defect rather than hang, and
   * that is measured rather than assumed: against the build that blanked the
   * page, React's nested-update limit stopped the loop when the address was
   * moved, but not when a click driven by `user-event` moved the focus — that
   * run spun for five minutes, and no test timeout can fire while it does.
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

  afterEach(() => {
    window.history.replaceState = realReplace
    vi.restoreAllMocks()
  })

  const address = (src: string, seg?: string): string =>
    `#/doc/zh-TW/${encodeURIComponent(src)}` + (seg ? `?seg=${seg}` : '')

  const reading = (src: string, seg?: string): string =>
    `#/read/zh-TW/${encodeURIComponent(src)}` + (seg ? `?seg=${seg}` : '')

  /**
   * Set the address a test opens on, and tell the router now. The browser
   * announces a hash change a task later, and the application resolves its
   * bootstrap in microtasks, so without this the first render can still be
   * reading the previous test's address.
   */
  const startAt = (hash: string): void => {
    window.location.hash = hash
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  }

  /** The margin names the segment it is about, and it is drawn only while the
   *  shell is — so finding it is also finding that the page is not blank. */
  const marginIsAbout = (id: string): void => {
    expect(screen.getByRole('heading', { name: id })).toBeTruthy()
    expect(screen.getByText('/3 translated')).toBeTruthy()
  }

  /** A task boundary, so a write that comes back a task later is seen before
   *  a state `waitFor` caught in passing is believed. */
  const settle = (): Promise<void> => new Promise(r => { setTimeout(r, 50) })

  const opened = async (): Promise<void> => {
    serve()
    render(<App />)
    await waitFor(() => { expect(screen.getByText('/3 translated')).toBeTruthy() }, { timeout: 4000 })
  }

  it('renders every segment moved to after the first, marks its row, and moves the address with it', async () => {
    startAt(address(doc.source))
    await opened()
    // Opened with no segment in the address, the ledger has nothing to land on,
    // and a click must not become the landing: that scrolled the list under the
    // pointer that made it.
    const scrolled = vi.spyOn(Element.prototype, 'scrollTo')
    const rows = render(<>{doc.segments.map(s => <SegmentRow key={s.id} seg={s} />)}</>)
    const row = (id: string): HTMLElement =>
      rows.container.querySelectorAll<HTMLElement>('.row')[doc.segments.findIndex(s => s.id === id)]!
    const marked = (): string[] =>
      [...rows.container.querySelectorAll<HTMLElement>('.row[aria-selected="true"]')]
        .map(r => doc.segments[[...rows.container.querySelectorAll('.row')].indexOf(r)]!.id)
    const user = userEvent.setup()
    const entries = window.history.length

    // The first move, then six more, mixing the three ways a row takes focus:
    // its source text takes the row's click; its field takes a focus and a
    // click together; and a keyboard move into the field is a focus alone.
    const steps: Array<[string, 'text' | 'field' | 'keyboard']> = [
      ['s0001', 'text'], ['s0003', 'text'], ['s0002', 'field'],
      ['s0003', 'keyboard'], ['s0001', 'text'], ['s0002', 'keyboard'], ['s0003', 'field'],
    ]
    for (const [id, how] of steps) {
      const before = writes
      if (how === 'keyboard') {
        act(() => { within(row(id)).getByRole('textbox').focus() })
      } else {
        await user.click(how === 'field'
          ? within(row(id)).getByRole('textbox')
          : within(row(id)).getByText(doc.segments.find(s => s.id === id)!.source))
      }
      await waitFor(() => {
        marginIsAbout(id)
        expect(window.location.hash).toBe(address(doc.source, id))
      })
      await settle()
      marginIsAbout(id)
      expect(window.location.hash).toBe(address(doc.source, id))
      expect(marked()).toEqual([id])
      expect(writes - before).toBe(1)
    }

    // A row replaces the address and never adds to history, or the back button
    // walks a chapter one paragraph at a time.
    expect(window.history.length).toBe(entries)
    expect(scrolled).not.toHaveBeenCalled()
  }, 15000)

  it('follows an address that names another segment, which is what Back, Forward and a link all do', async () => {
    startAt(address(doc.source, 's0001'))
    await opened()
    await waitFor(() => { marginIsAbout('s0001') })

    const before = writes
    window.location.hash = address(doc.source, 's0003')
    await waitFor(() => {
      marginIsAbout('s0003')
      expect(window.location.hash).toBe(address(doc.source, 's0003'))
    })
    await settle()
    marginIsAbout('s0003')
    // The person moved the address; nothing should move it back.
    expect(writes - before).toBe(0)
  }, 15000)

  it('does not carry a segment of one document into the next one opened', async () => {
    startAt(address(doc.source, 's0003'))
    await opened()
    await waitFor(() => { marginIsAbout('s0003') })

    // The rail's own link, to a document this page has not visited.
    await userEvent.setup().click(screen.getByRole('button', { name: /book\/ch2\.md/ }))
    await waitFor(() => { expect(screen.getByText('/2 translated')).toBeTruthy() }, { timeout: 4000 })
    await settle()

    // Ids restart at `s0001` in every document, so `s0003` here would be a
    // paragraph of this chapter that nobody chose.
    expect(window.location.hash).toBe(address(second.source))
    expect(screen.queryByRole('heading', { name: 's0003' })).toBeNull()
    expect(screen.getByText(/Choose a segment/)).toBeTruthy()
  }, 15000)

  /*
   * The effect that copied the store's focus into the address was also what put
   * a paragraph back on an address that had lost it, and ordinary trips relied
   * on that. With the segment kept in the address alone, each of them has to
   * carry it — and a version that forgot to lands a five-thousand-segment novel
   * at its top without failing anything above. Which of these three the build
   * that blanked the page also passes is recorded in `docs/decisions.md`.
   */

  it('opens the reading view at the paragraph, moves with a click there, and comes back to it', async () => {
    startAt(address(doc.source, 's0003'))
    await opened()
    await waitFor(() => { marginIsAbout('s0003') })
    const landed = vi.spyOn(Element.prototype, 'scrollIntoView')

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Read' }))
    await waitFor(() => {
      expect(window.location.hash).toBe(reading(doc.source, 's0003'))
      expect(document.getElementById('b-s0003')?.getAttribute('aria-current')).toBe('true')
      expect(landed).toHaveBeenCalledTimes(1)
    }, { timeout: 4000 })

    // A click in the reading view moves the address, and the page stays where
    // the pointer left it: the view scrolls on arrival and never under a click.
    // It replaces the address like a row does — a paragraph per history entry
    // is the back button walking a chapter one paragraph at a time.
    const entries = window.history.length
    const before = writes
    await user.click(document.getElementById('b-s0001')!)
    await waitFor(() => {
      expect(window.location.hash).toBe(reading(doc.source, 's0001'))
      expect(document.getElementById('b-s0001')?.getAttribute('aria-current')).toBe('true')
    })
    await settle()
    expect(landed).toHaveBeenCalledTimes(1)
    expect(writes - before).toBe(1)
    expect(window.history.length).toBe(entries)

    await user.click(screen.getByRole('button', { name: /Back to the ledger/ }))
    await waitFor(() => {
      expect(window.location.hash).toBe(address(doc.source, 's0001'))
      marginIsAbout('s0001')
    }, { timeout: 4000 })
  }, 15000)

  it('keeps the paragraph when the rail opens the document that is already open', async () => {
    startAt(address(doc.source, 's0002'))
    await opened()
    await waitFor(() => { marginIsAbout('s0002') })

    await userEvent.setup().click(screen.getByRole('button', { name: /book\/ch1\.md/ }))
    await settle()
    expect(window.location.hash).toBe(address(doc.source, 's0002'))
    marginIsAbout('s0002')
  }, 15000)

  it('returns to each document at its own paragraph, never the other one\'s', async () => {
    startAt(address(doc.source, 's0003'))
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

    await user.click(screen.getByRole('button', { name: /book\/ch3\.md/ }))
    await waitFor(() => {
      expect(window.location.hash).toBe(address(third.source, 's0001'))
      expect(screen.getByRole('heading', { name: 's0001' })).toBeTruthy()
    }, { timeout: 4000 })
  }, 15000)
})

/**
 * Leaving a document with words that were never written.
 *
 * Measured 2026-09-20 against `e6fc06d` in this runner, and on 2026-09-14 in
 * Chrome 153 against `lx web` on `f34298b`, whose code path is the same: open
 * `book/ch1.md`, type into a row's field without blurring it, then move the
 * address to another document. The whole request sequence was
 * `GET /api/state`, `GET /api/models`, `GET /api/doc`, `GET /api/doc` — **no
 * `POST /api/save` at all** — and the typed words were gone, with no prompt and
 * no line in the log.
 *
 * A click in the rail does not lose them, and that is luck rather than care: a
 * click takes DOM focus out of the textarea first, `onBlur` runs `save()`, and
 * `save()` reads `drafts` into a snapshot before its first `await`. Back,
 * Forward, a mouse side button and a hand-typed link move no focus, so nothing
 * blurs — and `beforeunload` does not fire for a fragment navigation. The luck
 * runs out anyway the moment that save fails, because `onBlur` neither awaits
 * it nor reads what it returned.
 *
 * **The field here is the ledger's own**, reached with `querySelector` rather
 * than a role query: `virtua` draws the row the address names inside a
 * `visibility: hidden` wrapper, so it is not in the accessibility tree and
 * `getByRole('textbox')` cannot see it.
 */
describe('leaving a document with words that were never written', () => {
  const ninth: DocResponse = {
    ...doc,
    source: 'book/ch9.md',
    report: { segments: 1, translated: 0, errors: 0, warnings: 0, by_rule: {} },
    segments: [
      { ...doc.segments[2]!, id: 's0001', source: 'Morning came late.', target: '', token: 'u1', issues: [] },
    ],
  }
  const wrote = { applied: 1, unknown: [], stored: {}, conflicts: {} }

  /**
   * As the block above's, plus `POST /api/save`, whose answer each test picks.
   *
   * A fixture answering that endpoint with `{}` would not be neutral: `save()`
   * reads `r.conflicts` **after** `drafts.forget(ids)`, so the `TypeError` that
   * follows leaves the map empty, its own `catch` swallows it, and every
   * assertion about words surviving would read as a pass.
   */
  const serve = (save: Answer = { body: wrote }): void => {
    const project: StateResponse = {
      ...state,
      docs: [...state.docs, { source: ninth.source, lang: 'zh-TW', total: 1, done: 0 }],
    }
    // **A cap, so that a loop fails instead of hanging.** Two measured shapes of
    // this change re-enter `open()` from `App.tsx`'s effect on every render —
    // `at` assigned after the flush, and `at` put back on a refusal — and each
    // one hung this file: every lap is a request answered in the same microtask
    // chain, so no timer, and with it no test timeout, could ever fire. Past the
    // cap a request is simply never answered, which ends the chain and lets the
    // assertions below fail where they stand. No test here comes near it.
    let requests = 0
    answering(call => {
      requests += 1
      if (requests > 150) return { body: {}, after: new Promise<void>(() => undefined) }
      const path = call.path
      if (path.startsWith('/api/state')) return { body: project }
      if (path.startsWith('/api/models')) return { body: { provider: 'local', configured: 'qwen', models: [], error: null } }
      if (path.startsWith('/api/save')) return save
      if (path.startsWith('/api/doc')) {
        const src = new URLSearchParams(path.split('?')[1]).get('src')
        return { body: src === ninth.source ? ninth : doc }
      }
      if (path.startsWith('/api/sentences')) return { body: { sentences: [] } }
      return { body: { source: '', lang: 'zh-TW', tone: null, ids: [], voice: '', voice_notes: [], algorithm: 'none', cutoff: 0, records: 0, segments: [] } }
    })
  }

  const address = (src: string, seg?: string): string =>
    `#/doc/zh-TW/${encodeURIComponent(src)}` + (seg ? `?seg=${seg}` : '')

  const startAt = (hash: string): void => {
    window.location.hash = hash
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  }

  const settle = (): Promise<void> => new Promise(r => { setTimeout(r, 50) })

  const field = (): HTMLTextAreaElement => {
    const el = document.querySelector<HTMLTextAreaElement>('.ledger textarea')
    if (!el) throw new Error('the ledger mounted no row for the segment in the address')
    return el
  }

  /** Open `book/ch1.md` at `s0003` and leave five characters in its field. */
  const typed = async (): Promise<void> => {
    startAt(address(doc.source, 's0003'))
    render(<App />)
    await waitFor(() => { expect(screen.getByText('/3 translated')).toBeTruthy() }, { timeout: 4000 })
    await userEvent.setup().type(field(), '燈還亮著。')
    expect(drafts.get('s0003')).toBe('燈還亮著。')
  }

  const readOf = (src: string): number =>
    calls.findIndex(c => c.path.startsWith('/api/doc') && c.path.includes(encodeURIComponent(src)))

  it('writes them to the document being left, before the next one is read', async () => {
    serve()
    await typed()

    // No blur and no click — the address simply moves, which is what Back,
    // Forward, a mouse side button and a hand-typed link all do.
    window.location.hash = address(ninth.source)
    await waitFor(() => { expect(screen.getByText('/1 translated')).toBeTruthy() }, { timeout: 4000 })
    await settle()

    const saves = callsTo('/api/save')
    expect(saves).toHaveLength(1)
    // **The body, never the path.** `postSave` posts to a fixed `/api/save` and
    // carries the document in its JSON, so an assertion on the path would hold
    // whatever the page had done.
    expect(saves[0]!.body).toMatchObject({
      src: doc.source, lang: 'zh-TW', targets: { s0003: '燈還亮著。' },
    })
    expect(calls.indexOf(saves[0]!)).toBeLessThan(readOf(ninth.source))
    expect(drafts.size()).toBe(0)
    // And the document being left is not read again: `save()` re-reads only the
    // document this page is still on, so a flush on the way out costs one round
    // trip rather than a second `GET /api/doc` of every segment it had.
    expect(calls.filter(c => c.path.startsWith('/api/doc') && c.path.includes(encodeURIComponent(doc.source))))
      .toHaveLength(1)
  }, 15000)

  it('keeps the ledger on screen while it writes the words', async () => {
    // `docLoading` goes up only once the words are written. Raised together with
    // `at` it would swap the ledger for the loading page — unmounting the
    // textarea with it — before the flush had read the field, which takes an IME
    // composition that has not yet produced an `input` event. jsdom has no IME;
    // what it can see is the ledger leaving while the words are in flight.
    //
    // **It asserts on the ledger and not on the field**, and that is jsdom
    // rather than a choice: the virtualized list mounts only the row the
    // address names here, and the address has already moved to another
    // document, so no row of this one is mounted whatever the code does. In a
    // browser the row being typed in is in the visible range and stays.
    let release: () => void = () => undefined
    serve({ body: wrote, after: new Promise<void>(r => { release = r }) })
    await typed()

    window.location.hash = address(ninth.source)
    await waitFor(() => { expect(callsTo('/api/save')).toHaveLength(1) })
    expect(document.querySelector('.ledger')).not.toBeNull()
    expect(screen.getByText('/3 translated')).toBeTruthy()
    expect(screen.queryByText(/reading book\/ch9\.md/)).toBeNull()

    release()
    await waitFor(() => { expect(screen.getByText('/1 translated')).toBeTruthy() }, { timeout: 4000 })
  }, 15000)

  it('does not leave while it holds wording it could not write, and writes no address', async () => {
    serve({ status: 400, body: { error: 'nothing is listening on 8787' } })
    await typed()
    const entries = window.history.length

    window.location.hash = address(ninth.source)
    await waitFor(() => {
      expect(screen.getByText(/could not be written to book\/ch1\.md/)).toBeTruthy()
    }, { timeout: 4000 })
    await settle()
    // The remedy names the document to go back to — not the one that was just
    // declined, which would send the reviewer round the same loop.
    expect(screen.getByText(/Open book\/ch1\.md again to get back to it/)).toBeTruthy()

    // One attempt, and then a stop — not a page that keeps trying on every render.
    expect(callsTo('/api/save')).toHaveLength(1)

    // The document the words belong to is still the one this page holds, and
    // the words are still in it.
    expect(useStore.getState().doc?.source).toBe(doc.source)
    expect(drafts.get('s0003')).toBe('燈還亮著。')
    expect(readOf(ninth.source)).toBe(-1)

    // **The address is left exactly where the reviewer put it.** Putting it back
    // is what this refusal will not do: a pushed entry is walked into by the
    // next Back press and pushed again, and a `replaceState` rewrites the entry
    // they just arrived at — so the Back after that fires no `hashchange` at
    // all, and the one after that eats another entry of their own history.
    // One entry, and it is the reviewer's own — the page added none.
    expect(window.location.hash).toBe(address(ninth.source))
    expect(window.history.length).toBe(entries + 1)
  }, 15000)

  it('never refuses to open the document the words belong to, so the refusal is not a trap', async () => {
    serve({ status: 400, body: { error: 'nothing is listening on 8787' } })
    await typed()
    window.location.hash = address(ninth.source)
    await waitFor(() => {
      expect(screen.getByText(/could not be written to book\/ch1\.md/)).toBeTruthy()
    }, { timeout: 4000 })

    // Back to the chapter **while the server is still refusing**. That is the
    // case that decides whether this is a trap: nothing is being left, so
    // nothing is refused, and the words are back in their own field.
    window.location.hash = address(doc.source, 's0003')
    await waitFor(() => { expect(screen.getByText('/3 translated')).toBeTruthy() }, { timeout: 4000 })
    await settle()
    expect(screen.queryByText(/could not be written/)).toBeNull()
    expect(drafts.get('s0003')).toBe('燈還亮著。')
    expect(field().value).toBe('燈還亮著。')

    // The server comes back, and the next move writes them where they were typed.
    serve()
    window.location.hash = address(ninth.source)
    await waitFor(() => { expect(screen.getByText('/1 translated')).toBeTruthy() }, { timeout: 4000 })
    await settle()
    expect((callsTo('/api/save').at(-1)!.body as { src: string; targets: Record<string, string> }))
      .toMatchObject({ src: doc.source, targets: { s0003: '燈還亮著。' } })
    expect(drafts.size()).toBe(0)
  }, 15000)

  it('still guards the tab close on the screen the refusal draws, which has no toolbar', async () => {
    // The guard used to live in the toolbar. The refusal does not draw one —
    // and neither do the reading view or either backend screen, so a draft that
    // failed to save has been walking into all three without it.
    serve({ status: 400, body: { error: 'nothing is listening on 8787' } })
    await typed()
    window.location.hash = address(ninth.source)
    await waitFor(() => {
      expect(screen.getByText(/could not be written to book\/ch1\.md/)).toBeTruthy()
    }, { timeout: 4000 })
    expect(screen.queryByRole('button', { name: 'Re-extract' })).toBeNull()

    const closing = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(closing)
    expect(closing.defaultPrevented).toBe(true)
  }, 15000)

  it('lets the tab close once the words are written', async () => {
    // The other side of the same guard. One that asked on every close after the
    // first keystroke would teach a reviewer to click through the one that
    // matters.
    serve()
    await typed()
    window.location.hash = address(ninth.source)
    await waitFor(() => { expect(screen.getByText('/1 translated')).toBeTruthy() }, { timeout: 4000 })
    await settle()

    const closing = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(closing)
    expect(closing.defaultPrevented).toBe(false)
  }, 15000)

  it('does not refuse over a field of spaces, which is not wording either', async () => {
    // `save()` holds whitespace back exactly as it holds an empty field back, so
    // the question "is there anything left to lose" must be the same predicate —
    // a second spelling of it that counted spaces as words would decline to
    // leave over something the server will never store.
    serve()
    startAt(address(doc.source, 's0003'))
    render(<App />)
    await waitFor(() => { expect(screen.getByText('/3 translated')).toBeTruthy() }, { timeout: 4000 })
    await userEvent.setup().type(field(), '   ')
    expect(drafts.get('s0003')).toBe('   ')

    window.location.hash = address(ninth.source)
    await waitFor(() => { expect(screen.getByText('/1 translated')).toBeTruthy() }, { timeout: 4000 })
    await settle()
    expect(screen.queryByText(/could not be written/)).toBeNull()
    expect(callsTo('/api/save')).toHaveLength(0)
  }, 15000)

  it('does not refuse over an emptied field, which is not wording', async () => {
    // The server refuses an empty target for the whole request, so `save()`
    // holds one back for ever. Refusing to leave over one would trap a reviewer
    // who had merely cleared a field, with nothing on the page to undo it.
    serve()
    startAt(address(doc.source, 's0002'))
    render(<App />)
    await waitFor(() => { expect(screen.getByText('/3 translated')).toBeTruthy() }, { timeout: 4000 })
    await userEvent.setup().clear(field())
    expect(drafts.get('s0002')).toBe('')

    window.location.hash = address(ninth.source)
    await waitFor(() => { expect(screen.getByText('/1 translated')).toBeTruthy() }, { timeout: 4000 })
    await settle()
    expect(callsTo('/api/save')).toHaveLength(0)
    expect(drafts.size()).toBe(0)
  }, 15000)
})

/**
 * The rail's *Not yet extracted* entry, which extracted the wrong document.
 *
 * HANDOFF-088, measured 2026-09-14 in Chrome 153 against `lx web` on `f34298b`:
 * with a document open and an untracked source listed, a click on the untracked
 * entry sent exactly one request, `POST /api/extract` naming **the document that
 * was open**, while the log read `— extract <the clicked file> —`, and the
 * clicked file stayed untracked. The button called `store.open()` beside the
 * address, `App.tsx`'s effect put the addressed document back a render later,
 * and the extract read `shown()` — still the open document.
 *
 * The entry is a link now, like every other in the rail, and the extract is
 * offered on the page it lands on. The fixture answers as the server does:
 * `GET /api/doc` for a file with no state is a 400 carrying `no state for …`,
 * until an extract has made one.
 */
describe('the rail\'s Not yet extracted entry', () => {
  const fresh: DocResponse = {
    ...doc,
    source: 'docs/untracked.md',
    report: { segments: 4, translated: 0, errors: 0, warnings: 0, by_rule: {} },
    segments: ['s0001', 's0002', 's0003', 's0004'].map((id, i) => (
      { ...doc.segments[2]!, id, source: `Line ${i + 1}.`, target: '', token: `n${i}`, issues: [] }
    )),
  }
  const second: DocResponse = {
    ...doc,
    source: 'book/ch2.md',
    report: { segments: 2, translated: 0, errors: 0, warnings: 0, by_rule: {} },
    segments: [
      { ...doc.segments[2]!, id: 's0001', source: 'Morning came late.', target: '', token: 'u1', issues: [] },
      { ...doc.segments[2]!, id: 's0002', source: 'Nobody spoke.', target: '', token: 'u2', issues: [] },
    ],
  }
  const extracted = { segments: 4, reused: 0, rejected: 0, kept: [], ambiguous: [], replaced: [], waived_source: [] }
  const wrote = { applied: 1, unknown: [], stored: {}, conflicts: {} }

  /** Whether the untracked file has state yet. Flipped by the extract the
   *  server accepts for it, and read by every later answer. */
  let made = false

  /**
   * `listed` is what `GET /api/state` says about the file, `made` what the
   * server really holds — two facts a terminal can pull apart, and one test here
   * is about exactly that.
   */
  const serve = (over: { extract?: Answer; save?: Answer; made?: boolean; listed?: () => boolean } = {}): void => {
    made = over.made ?? false
    const listed = over.listed ?? (() => !made)
    let requests = 0
    answering(call => {
      requests += 1
      // The cap `leaving a document…` explains: a loop fails rather than hangs.
      if (requests > 150) return { body: {}, after: new Promise<void>(() => undefined) }
      const path = call.path
      if (path.startsWith('/api/state')) {
        const project: StateResponse = {
          ...state,
          docs: [
            ...state.docs,
            { source: second.source, lang: 'zh-TW', total: 2, done: 0 },
            ...(listed() ? [] : [{ source: fresh.source, lang: 'zh-TW', total: 4, done: 0 }]),
          ],
          untracked: listed() ? [{ source: fresh.source, lang: 'zh-TW' }] : [],
        }
        return { body: project }
      }
      if (path.startsWith('/api/models')) return { body: { provider: 'local', configured: 'qwen', models: [], error: null } }
      if (path.startsWith('/api/save')) return over.save ?? { body: wrote }
      if (path.startsWith('/api/extract')) {
        const answer = over.extract ?? { body: extracted }
        const ok = (answer.status ?? 200) < 300
        const src = (call.body as { src?: string } | null)?.src
        if (ok && src === fresh.source) {
          if (answer.after) void answer.after.then(() => { made = true })
          else made = true
        }
        return answer
      }
      if (path.startsWith('/api/doc')) {
        const src = new URLSearchParams(path.split('?')[1]).get('src')
        if (src === fresh.source) {
          return made
            ? { body: fresh }
            : { status: 400, body: { error: `no state for ${fresh.source} [zh-TW] — run \`lx extract ${fresh.source} --lang zh-TW\` first` } }
        }
        return { body: src === second.source ? second : doc }
      }
      if (path.startsWith('/api/sentences')) return { body: { sentences: [] } }
      return { body: { source: '', lang: 'zh-TW', tone: null, ids: [], voice: '', voice_notes: [], algorithm: 'none', cutoff: 0, records: 0, segments: [] } }
    })
  }

  const address = (src: string, seg?: string): string =>
    `#/doc/zh-TW/${encodeURIComponent(src)}` + (seg ? `?seg=${seg}` : '')

  const startAt = (hash: string): void => {
    window.location.hash = hash
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  }

  const settle = (): Promise<void> => new Promise(r => { setTimeout(r, 50) })

  const opened = async (hash = address(doc.source, 's0003')): Promise<void> => {
    startAt(hash)
    render(<App />)
    await waitFor(() => { expect(screen.getByText('/3 translated')).toBeTruthy() }, { timeout: 4000 })
  }

  // Matches the entry under either label, `extract` before HANDOFF-088 and `not
  // extracted` after, so a run against the parent fails on what the click did
  // rather than on how the button was spelled. A tracked entry's label is a count.
  const entry = (): HTMLElement => screen.getByRole('button', { name: /docs\/untracked\.md.*extract/ })
  const offer = (): HTMLButtonElement => screen.getByRole('button', { name: `Extract ${fresh.source}` })
  const offered = (): boolean => screen.queryByRole('button', { name: `Extract ${fresh.source}` }) !== null

  /** Click the entry and wait for the page it leads to. */
  const landed = async (): Promise<void> => {
    await userEvent.setup().click(entry())
    await waitFor(() => { expect(offer()).toBeTruthy() }, { timeout: 4000 })
  }

  const extracts = (): unknown[] => callsTo('/api/extract').map(c => c.body)

  const logSays = (text: string): boolean =>
    useStore.getState().log.some(l => l.text.includes(text))

  it('is a link: it opens the file\'s page, extracts nothing, and adds one history entry', async () => {
    serve()
    await opened()
    const entries = window.history.length

    await userEvent.setup().click(entry())
    await settle()
    // A link sends no act. Against `83a865b` this is where it fails, and the
    // failure is the defect as measured: one extract, naming `book/ch1.md`.
    expect(extracts()).toEqual([])
    await waitFor(() => { expect(offer()).toBeTruthy() }, { timeout: 4000 })
    expect(window.location.hash).toBe(address(fresh.source))
    expect(screen.getByRole('heading', { name: fresh.source })).toBeTruthy()
    expect(window.history.length).toBe(entries + 1)
    expect(entry().textContent).toContain('not extracted')
    // And following it is not an error: the page explains itself, so the
    // server's `no state for …` is shown there and not logged in red.
    expect(screen.getByText(/^no state for docs\/untracked\.md/)).toBeTruthy()
    expect(useStore.getState().log.some(l => l.level === 'bad')).toBe(false)

    // Back returns to the chapter and its paragraph.
    window.history.back()
    await waitFor(() => {
      expect(window.location.hash).toBe(address(doc.source, 's0003'))
      expect(screen.getByText('/3 translated')).toBeTruthy()
    }, { timeout: 4000 })
  }, 15000)

  it('extracts the file its page is about, not the document that was open, and then shows it', async () => {
    serve()
    await opened()
    await landed()

    await userEvent.setup().click(offer())
    await waitFor(() => { expect(extracts()).toHaveLength(1) }, { timeout: 4000 })
    // The request names the file that was clicked — as a plain extract: a first
    // extract has no register to keep and nothing to reset.
    expect(extracts()).toEqual([{ src: fresh.source, lang: 'zh-TW' }])

    await waitFor(() => { expect(screen.getByText('/4 translated')).toBeTruthy() }, { timeout: 4000 })
    await settle()
    expect(extracts()).toHaveLength(1)
    expect(window.location.hash).toBe(address(fresh.source))
    // The log names what was extracted, and says what the extract did.
    expect(logSays(`— extract ${fresh.source} [zh-TW] —`)).toBe(true)
    expect(logSays('4 segments, 0 reused')).toBe(true)
    // The document that was open was read once, by the address that opened it,
    // and nothing re-parsed it.
    expect(calls.filter(c => c.path.startsWith('/api/doc') && c.path.includes(encodeURIComponent(doc.source))))
      .toHaveLength(1)
    // It is tracked now: the rail lists it with the others.
    expect(screen.queryByRole('button', { name: /not extracted/ })).toBeNull()
  }, 15000)

  it('opens a file a terminal has extracted since the list was read, and offers no extract', async () => {
    // The list is a snapshot taken at startup; the server already holds state.
    // The page's own read is the fresh fact, and it succeeds.
    serve({ made: true, listed: () => true })
    await opened()

    await userEvent.setup().click(entry())
    await waitFor(() => { expect(screen.getByText('/4 translated')).toBeTruthy() }, { timeout: 4000 })
    await settle()
    expect(offered()).toBe(false)
    expect(extracts()).toEqual([])
  }, 15000)

  it('offers no extract on a page that declined to leave the chapter, and the chapter\'s words survive', async () => {
    // The test HANDOFF-087 named for this package: `POST /api/save` failing
    // while a dirty field is open. The old button re-extracted the chapter here
    // — and a re-extract marks every unsaved word in it unwritable, so the words
    // went as well as the wrong file being parsed.
    serve({ save: { status: 400, body: { error: 'nothing is listening on 8787' } } })
    await opened()
    await userEvent.setup().type(document.querySelector<HTMLTextAreaElement>('.ledger textarea')!, '燈還亮著。')

    await userEvent.setup().click(entry())
    await waitFor(() => {
      expect(screen.getByText(/could not be written to book\/ch1\.md/)).toBeTruthy()
    }, { timeout: 4000 })
    await settle()

    // The page did not read the file — it declined to leave — so it has no
    // fresh fact to offer an extract on.
    expect(offered()).toBe(false)
    expect(extracts()).toEqual([])
    expect(drafts.get('s0003')).toBe('燈還亮著。')
    expect(drafts.stranded()).toBe(false)
    expect(useStore.getState().doc?.source).toBe(doc.source)
  }, 15000)

  it('stays on the file\'s page when the extract is refused, with the reason in the log', async () => {
    serve({ extract: { status: 400, body: { error: 'docs/untracked.md: not UTF-8 and no configured encoding reads it' } } })
    await opened()
    await landed()

    await userEvent.setup().click(offer())
    await waitFor(() => { expect(logSays('not UTF-8')).toBe(true) }, { timeout: 4000 })
    await settle()
    expect(window.location.hash).toBe(address(fresh.source))
    expect(offer().disabled).toBe(false)
    // Only the landing read: the click asks the project's list, not the file,
    // and nothing re-read a file the server said it could not make.
    expect(callsTo('/api/doc').filter(c => c.path.includes(encodeURIComponent(fresh.source)))).toHaveLength(1)
  }, 15000)

  it('waits for a run in flight, rather than logging an extract it will not send', async () => {
    serve()
    await opened()
    await landed()
    act(() => { useStore.setState({ running: true }) })

    expect(offer().disabled).toBe(true)
    await userEvent.setup().click(offer())
    await settle()
    expect(extracts()).toHaveLength(0)
    expect(logSays('— extract')).toBe(false)
  }, 15000)

  it('offers the same page to a hand-typed link, and adds no history entry of its own', async () => {
    // Slashes left bare, which `parse` accepts on purpose. The extract re-reads
    // what the address names, and nothing navigates — so the address stays
    // spelled the way the reviewer typed it.
    serve()
    startAt('#/doc/zh-TW/docs/untracked.md')
    render(<App />)
    await waitFor(() => { expect(offer()).toBeTruthy() }, { timeout: 4000 })
    const entries = window.history.length

    await userEvent.setup().click(offer())
    await waitFor(() => { expect(screen.getByText('/4 translated')).toBeTruthy() }, { timeout: 4000 })
    await settle()
    expect(extracts()).toEqual([{ src: fresh.source, lang: 'zh-TW' }])
    expect(window.location.hash).toBe('#/doc/zh-TW/docs/untracked.md')
    expect(window.history.length).toBe(entries)
  }, 15000)

  it('does not pull back a reviewer who left the page while it was extracting', async () => {
    let release: () => void = () => undefined
    serve({ extract: { body: extracted, after: new Promise<void>(r => { release = r }) } })
    await opened()
    await landed()
    const user = userEvent.setup()

    await user.click(offer())
    await waitFor(() => { expect(extracts()).toHaveLength(1) })
    await user.click(screen.getByRole('button', { name: /book\/ch2\.md/ }))
    await waitFor(() => { expect(screen.getByText('/2 translated')).toBeTruthy() }, { timeout: 4000 })

    release()
    await waitFor(() => { expect(logSays('4 segments, 0 reused')).toBe(true) }, { timeout: 4000 })
    await settle()
    expect(window.location.hash).toBe(address(second.source))
    expect(screen.getByText('/2 translated')).toBeTruthy()
    // It is made, and listed as made — and not read after the extract, because
    // nothing asked: the landing read is the only one.
    expect(screen.queryByRole('button', { name: /not extracted/ })).toBeNull()
    expect(callsTo('/api/doc').filter(c => c.path.includes(encodeURIComponent(fresh.source)))).toHaveLength(1)
  }, 15000)
})

/**
 * The toolbar's two extracts, driven through the toolbar.
 *
 * Every store-level test calls `extract` and `startOver` with an address it
 * builds itself, so the address these controls pass was covered by nothing: two
 * independent mutation lanes swapped its fields, hard-coded its language and
 * sent the document's old register in place of the one typed, and all of it
 * survived (HANDOFF-088). These click the controls instead.
 *
 * jsdom 30 has no `HTMLDialogElement.showModal`, so it is stood in for here —
 * locally, because nothing else in the suite opens a dialog and a harness-wide
 * stand-in would answer for tests that never asked it to.
 */
describe('the toolbar\'s re-extract and start over', () => {
  const japanese: DocResponse = { ...doc, lang: 'ja' }
  const extracted = { segments: 3, reused: 3, rejected: 0, kept: [], ambiguous: [], replaced: [], waived_source: [] }
  const empty: DocResponse = {
    ...doc,
    source: 'book/ch2.md',
    report: { segments: 1, translated: 0, errors: 0, warnings: 0, by_rule: {} },
    segments: [{ ...doc.segments[2]!, id: 's0001', source: 'Morning came late.', target: '', token: 'u1', issues: [] }],
  }

  const real = {
    showModal: Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'showModal'),
    close: Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'close'),
  }
  beforeEach(() => {
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
      configurable: true,
      value: function showModal(this: HTMLDialogElement) { this.setAttribute('open', '') },
    })
    Object.defineProperty(HTMLDialogElement.prototype, 'close', {
      configurable: true,
      value: function close(this: HTMLDialogElement) { this.removeAttribute('open') },
    })
  })
  afterEach(() => {
    for (const [name, d] of Object.entries(real)) {
      if (d) Object.defineProperty(HTMLDialogElement.prototype, name, d)
      else delete (HTMLDialogElement.prototype as unknown as Record<string, unknown>)[name]
    }
  })

  /** `slow`, when given, holds the n-th read of `book/ch1.md` until it settles. */
  const serve = (first: DocResponse, slow?: { n: number; until: Promise<void> }): void => {
    const reads = new Map<string, number>()
    answering(call => {
      const path = call.path
      if (path.startsWith('/api/state')) {
        return { body: { ...state, docs: [...state.docs, { source: empty.source, lang: 'zh-TW', total: 1, done: 0 }] } }
      }
      if (path.startsWith('/api/models')) return { body: { provider: 'local', configured: 'qwen', models: [], error: null } }
      if (path.startsWith('/api/extract')) return { body: extracted }
      if (path.startsWith('/api/doc')) {
        const src = new URLSearchParams(path.split('?')[1]).get('src') ?? ''
        const n = (reads.get(src) ?? 0) + 1
        reads.set(src, n)
        if (src === empty.source) return { body: empty }
        return slow && n === slow.n ? { body: first, after: slow.until } : { body: first }
      }
      if (path.startsWith('/api/sentences')) return { body: { sentences: [] } }
      return { body: { source: '', lang: first.lang, tone: null, ids: [], voice: '', voice_notes: [], algorithm: 'none', cutoff: 0, records: 0, segments: [] } }
    })
  }

  const startAt = (hash: string): void => {
    window.location.hash = hash
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  }

  const opened = async (d: DocResponse): Promise<void> => {
    startAt(`#/doc/${d.lang}/${encodeURIComponent(d.source)}`)
    render(<App />)
    await waitFor(() => { expect(screen.getByText(`/${d.report.segments} translated`)).toBeTruthy() }, { timeout: 4000 })
  }

  /** The dialog's own button, not the toolbar's control of the same name. */
  const answer = async (label: string): Promise<void> => {
    await waitFor(() => { expect(document.querySelector('dialog[open]')).not.toBeNull() }, { timeout: 4000 })
    const dialog = document.querySelector<HTMLDialogElement>('dialog[open]')!
    await userEvent.setup().click(within(dialog).getByRole('button', { name: label }))
  }

  it('re-extracts the document its dialog named, in that document\'s language', async () => {
    serve(japanese)
    await opened(japanese)

    await userEvent.setup().click(screen.getByRole('button', { name: 'Re-extract' }))
    await answer('Re-extract')
    await waitFor(() => { expect(callsTo('/api/extract')).toHaveLength(1) }, { timeout: 4000 })
    expect(callsTo('/api/extract')[0]!.body).toEqual({ src: doc.source, lang: 'ja' })
  }, 15000)

  it('starts over in the register the reviewer typed, on the document its dialog named', async () => {
    serve(japanese)
    await opened(japanese)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: /Start over in another register/ }))
    await user.type(screen.getByPlaceholderText('type a register'), 'plain')
    await user.click(screen.getByRole('button', { name: 'Start over…' }))
    await answer('Discard and re-extract')
    await waitFor(() => { expect(callsTo('/api/extract')).toHaveLength(1) }, { timeout: 4000 })
    // Not the register the document is frozen in (`literary`), which is shown
    // beside the field precisely so it is not mistaken for the value.
    expect(callsTo('/api/extract')[0]!.body).toEqual({ src: doc.source, lang: 'ja', reset: true, tone: 'plain' })
  }, 15000)

  it('re-extracts the document its dialog named when Back has moved the page under the dialog', async () => {
    // A modal dialog does not stop Back. The extract used to read the screen, so
    // confirming a dialog that named one chapter re-parsed the chapter arrived
    // at (measured against `83a865b` by the review of HANDOFF-088).
    serve(doc)
    await opened(doc)

    await userEvent.setup().click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(document.querySelector('dialog[open]')).not.toBeNull() }, { timeout: 4000 })
    window.location.hash = `#/doc/zh-TW/${encodeURIComponent(empty.source)}`
    await waitFor(() => { expect(useStore.getState().doc?.source).toBe(empty.source) }, { timeout: 4000 })
    await answer('Re-extract')
    await waitFor(() => { expect(callsTo('/api/extract')).toHaveLength(1) }, { timeout: 4000 })
    expect(callsTo('/api/extract')[0]!.body).toEqual({ src: doc.source, lang: 'zh-TW' })
    // And the page stays where the reviewer went.
    await new Promise(r => { setTimeout(r, 100) })
    expect(useStore.getState().doc?.source).toBe(empty.source)
  }, 15000)

  it('starts over the document its dialog named when Back has moved the page under the dialog', async () => {
    // The same door with the destructive control behind it: against `83a865b`
    // the reset went to the chapter arrived at, and a reset discards every
    // translation a document holds.
    serve(doc)
    await opened(doc)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: /Start over in another register/ }))
    await user.type(screen.getByPlaceholderText('type a register'), 'plain')
    await user.click(screen.getByRole('button', { name: 'Start over…' }))
    await waitFor(() => { expect(document.querySelector('dialog[open]')).not.toBeNull() }, { timeout: 4000 })
    window.location.hash = `#/doc/zh-TW/${encodeURIComponent(empty.source)}`
    await waitFor(() => { expect(useStore.getState().doc?.source).toBe(empty.source) }, { timeout: 4000 })
    await answer('Discard and re-extract')
    await waitFor(() => { expect(callsTo('/api/extract')).toHaveLength(1) }, { timeout: 4000 })
    expect(callsTo('/api/extract')[0]!.body).toEqual({ src: doc.source, lang: 'zh-TW', reset: true, tone: 'plain' })
  }, 15000)

  it('does not decide its confirmation from a document the reviewer moved to', async () => {
    // Its `refresh()` re-reads whatever is on screen. Held long enough for the
    // reviewer to open an untranslated chapter, the count it then read was that
    // chapter's — zero — so the dialog was skipped and a document holding
    // translations was re-parsed unasked (found by the review of HANDOFF-088).
    let release: () => void = () => undefined
    serve(doc, { n: 2, until: new Promise<void>(r => { release = r }) })
    await opened(doc)
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(callsTo('/api/doc')).toHaveLength(2) })
    await user.click(screen.getByRole('button', { name: /book\/ch2\.md/ }))
    await waitFor(() => { expect(screen.getByText('/1 translated')).toBeTruthy() }, { timeout: 4000 })
    release()
    await new Promise(r => { setTimeout(r, 200) })

    expect(document.querySelector('dialog[open]')).toBeNull()
    expect(callsTo('/api/extract')).toEqual([])
    expect(useStore.getState().log.some(l => l.level === 'warn' && l.text.includes('was not re-extracted'))).toBe(true)
  }, 15000)

  it('logs no header over an extract a run has started in front of', async () => {
    // The run buttons stay enabled while Re-extract saves and re-reads, so a
    // run can start in that window, and `reExtract` then declines the extract.
    let release: () => void = () => undefined
    serve(doc, { n: 2, until: new Promise<void>(r => { release = r }) })
    await opened(doc)

    await userEvent.setup().click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(callsTo('/api/doc')).toHaveLength(2) })
    act(() => { useStore.setState({ running: true }) })
    release()
    await answer('Re-extract')
    await new Promise(r => { setTimeout(r, 100) })

    expect(callsTo('/api/extract')).toEqual([])
    expect(useStore.getState().log.some(l => l.text.startsWith('— re-extract'))).toBe(false)
    expect(useStore.getState().log.some(l => l.level === 'warn' && l.text.includes('a run started'))).toBe(true)
  }, 15000)
})

/**
 * The margin after a re-parse.
 *
 * Ids are reassigned from `s0001` on every parse, and the margin's style cache
 * was scoped `src|lang` — which no re-extract changes — with its effect keyed on
 * the id alone. So once a re-extract put different text under the id the
 * address names, the margin went on showing what the model is told about the
 * paragraph that *used to* carry it, beside the one that does now. Traced by
 * HANDOFF-088 from the code; these reproduce it.
 */
describe('the margin after a re-parse', () => {
  const extracted = { segments: 3, reused: 3, rejected: 0, kept: [], ambiguous: [], replaced: [], waived_source: [] }

  /** `after` is what the next `GET /api/doc` answers once a re-extract lands. */
  const serve = (after: DocResponse): void => {
    let parsed = false
    answering(call => {
      const path = call.path
      if (path.startsWith('/api/state')) return { body: state }
      if (path.startsWith('/api/models')) return { body: { provider: 'local', configured: 'qwen', models: [], error: null } }
      if (path.startsWith('/api/extract')) { parsed = true; return { body: extracted } }
      if (path.startsWith('/api/doc')) return { body: parsed ? after : doc }
      if (path.startsWith('/api/style')) {
        // What the server answers is a function of the paragraph's text and the
        // document's register, so the fixture answers from those two.
        const now = parsed ? after : doc
        const id = (call.body as { ids: string[] }).ids[0]
        const seg = now.segments.find(s => s.id === id)
        return {
          body: {
            source: now.source, lang: 'zh-TW', tone: now.tone, ids: [id],
            voice: `brief for ${now.tone ?? 'none'}`,
            voice_notes: [{ names: ['note'], notes: `about: ${seg?.source ?? '?'}` }],
          },
        }
      }
      return { body: { algorithm: 'none', cutoff: 0, records: 0, segments: [] } }
    })
  }

  const startAt = (hash: string): void => {
    window.location.hash = hash
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  }

  const opened = async (): Promise<void> => {
    startAt('#/doc/zh-TW/book%2Fch1.md?seg=s0001')
    render(<App />)
    await waitFor(() => { expect(screen.getByText('about: Chapter 1')).toBeTruthy() }, { timeout: 4000 })
  }

  it('asks again about the paragraph that now carries the id', async () => {
    // A paragraph inserted at the top: every id now names the paragraph before
    // the one it named.
    const moved: DocResponse = {
      ...doc,
      segments: [
        { ...doc.segments[1]!, id: 's0001', source: 'A new opening line.', token: 'm1' },
        { ...doc.segments[0]!, id: 's0002', token: 'm2' },
        { ...doc.segments[1]!, id: 's0003', token: 'm3' },
      ],
    }
    serve(moved)
    await opened()

    await act(async () => { await useStore.getState().extract({ src: doc.source, lang: 'zh-TW' }) })
    await waitFor(() => { expect(screen.getByText('about: A new opening line.')).toBeTruthy() }, { timeout: 4000 })
    expect(screen.queryByText('about: Chapter 1')).toBeNull()
  }, 15000)

  it('asks again after a start-over, because the register is part of the answer', async () => {
    const plain: DocResponse = { ...doc, tone: 'plain' }
    serve(plain)
    await opened()
    expect(screen.getByText('brief for literary')).toBeTruthy()

    await act(async () => { await useStore.getState().startOver({ src: doc.source, lang: 'zh-TW' }, 'plain') })
    await waitFor(() => { expect(screen.getByText('brief for plain')).toBeTruthy() }, { timeout: 4000 })
  }, 15000)

  it('does not ask again when the re-parse left the paragraph where it was', async () => {
    serve(doc)
    await opened()
    const asked = callsTo('/api/style').length

    await act(async () => { await useStore.getState().extract({ src: doc.source, lang: 'zh-TW' }) })
    await new Promise(r => { setTimeout(r, 900) })
    expect(screen.getByText('about: Chapter 1')).toBeTruthy()
    // The cache is for exactly this: a second look at a paragraph is free.
    expect(callsTo('/api/style')).toHaveLength(asked)
  }, 15000)

  it('does not keep a reply asked in the old register as the new register\'s answer', async () => {
    // The ledger stays mounted through a start-over's request, so a reviewer
    // who moves meanwhile asks about a paragraph in the register that is about
    // to go — and the reply can land after the page has emptied the cache for
    // the next one. Written into whichever map was current when it landed, it
    // was served as the new register's brief on the next look at that
    // paragraph. Found by the review of HANDOFF-088, and older than it.
    let tone = 'literary'
    let releaseExtract: () => void = () => undefined
    let holdNextStyle = false
    const heldStyles: Array<() => void> = []
    answering(call => {
      const path = call.path
      if (path.startsWith('/api/state')) return { body: state }
      if (path.startsWith('/api/models')) return { body: { provider: 'local', configured: 'qwen', models: [], error: null } }
      if (path.startsWith('/api/extract')) {
        return { body: extracted, after: new Promise<void>(r => { releaseExtract = () => { tone = 'plain'; r() } }) }
      }
      if (path.startsWith('/api/doc')) return { body: { ...doc, tone } }
      if (path.startsWith('/api/style')) {
        const id = (call.body as { ids: string[] }).ids[0]
        // Answered in the register the server holds when the request arrives.
        const body = { source: doc.source, lang: 'zh-TW', tone, ids: [id], voice: `brief for ${tone}`, voice_notes: [] }
        if (!holdNextStyle) return { body }
        holdNextStyle = false
        return { body, after: new Promise<void>(r => { heldStyles.push(r) }) }
      }
      return { body: { algorithm: 'none', cutoff: 0, records: 0, segments: [] } }
    })
    startAt('#/doc/zh-TW/book%2Fch1.md?seg=s0001')
    render(<App />)
    await waitFor(() => { expect(screen.getByText('brief for literary')).toBeTruthy() }, { timeout: 4000 })

    let done: Promise<boolean> = Promise.resolve(true)
    act(() => { done = useStore.getState().startOver({ src: doc.source, lang: 'zh-TW' }, 'plain') })
    await waitFor(() => { expect(callsTo('/api/extract')).toHaveLength(1) })
    holdNextStyle = true
    act(() => { routes.focus(doc.source, 'zh-TW', 's0002') })
    await waitFor(() => { expect(heldStyles).toHaveLength(1) }, { timeout: 3000 })

    releaseExtract()
    await act(async () => { await done })
    await waitFor(() => { expect(useStore.getState().doc?.tone).toBe('plain') }, { timeout: 3000 })
    await act(async () => { heldStyles[0]!(); await new Promise(r => { setTimeout(r, 20) }) })

    act(() => { routes.focus(doc.source, 'zh-TW', 's0003') })
    await waitFor(() => { expect(screen.getByText('brief for plain')).toBeTruthy() }, { timeout: 3000 })
    act(() => { routes.focus(doc.source, 'zh-TW', 's0002') })
    await new Promise(r => { setTimeout(r, 1000) })
    expect(screen.queryByText('brief for literary')).toBeNull()
    expect(screen.getByText('brief for plain')).toBeTruthy()
  }, 20000)
})

/**
 * What the page acts on is what it has just read — HANDOFF-088's second review.
 *
 * The first review broke a design that navigated by itself; the second broke
 * the one that replaced it on a different axis: **how old a fact is when an act
 * rests on it.** A startup list that a terminal has made stale, a read of a file
 * made before a trip to the backend screens, a refresh that lands after the page
 * has declined to show its document, an open overtaken by a newer one. Each test
 * here failed against `082d0fb`, and those marked *older* failed against
 * `83a865b` as well.
 *
 * The fixture models the server and the startup list apart — `world.made` is
 * what the server holds, `world.listed` what `GET /api/state` says — because the
 * difference between them is what half of these are about.
 */
describe('what the page acts on is what it has just read', () => {
  const fresh: DocResponse = {
    ...doc,
    source: 'docs/untracked.md',
    report: { segments: 4, translated: 0, errors: 0, warnings: 0, by_rule: {} },
    segments: ['s0001', 's0002', 's0003', 's0004'].map((id, i) => (
      { ...doc.segments[2]!, id, source: `Line ${i + 1}.`, target: '', token: `n${i}`, issues: [] }
    )),
  }
  const freshTranslated: DocResponse = {
    ...fresh,
    report: { segments: 4, translated: 4, errors: 0, warnings: 0, by_rule: {} },
    segments: fresh.segments.map((s, i) => ({ ...s, target: `第${i + 1}行。`, status: 'translated', origin: 'human', token: `x${i}`, issues: [] })),
  }
  const second: DocResponse = {
    ...doc,
    source: 'book/ch2.md',
    report: { segments: 2, translated: 0, errors: 0, warnings: 0, by_rule: {} },
    segments: [
      { ...doc.segments[2]!, id: 's0001', source: 'Morning came late.', target: '', token: 'u1', issues: [] },
      { ...doc.segments[2]!, id: 's0002', source: 'Nobody spoke.', target: '', token: 'u2', issues: [] },
    ],
  }
  const third: DocResponse = { ...second, source: 'book/ch3.md' }
  const untranslated: DocResponse = {
    ...doc,
    report: { ...doc.report, translated: 0 },
    segments: doc.segments.map(s => ({ ...s, target: '', status: 'pending' as const, origin: null, review: null })),
  }
  const extracted = { segments: 4, reused: 0, rejected: 0, kept: [], ambiguous: [], replaced: [], waived_source: [] }
  const wrote = { applied: 1, unknown: [], stored: {}, conflicts: {} }
  const refused: Answer = { status: 400, body: { error: 'nothing is listening on 8787' } }

  const world = { made: false, listed: true, held: fresh }

  /** `read(src, n)` may answer the n-th `GET /api/doc` of a file, `save(n)` the
   *  n-th `POST /api/save`, `listing(n)` the n-th `GET /api/state` (its body is
   *  still the world's, read when the reply is sent); `base` is what
   *  `book/ch1.md` reads as. */
  const serve = (over: {
    read?: (src: string, n: number) => Answer | null
    save?: (n: number, body: unknown) => Answer | null
    listing?: (n: number) => Promise<void> | null
    hold?: (n: number) => Answer | null
    onExtract?: () => void
    base?: DocResponse
  } = {}): void => {
    const reads = new Map<string, number>()
    let saves = 0
    let listings = 0
    let holds = 0
    let requests = 0
    const project = (): StateResponse => ({
      ...state,
      docs: [
        ...state.docs,
        { source: second.source, lang: 'zh-TW', total: 2, done: 0 },
        { source: third.source, lang: 'zh-TW', total: 2, done: 0 },
        ...(world.listed ? [] : [{ source: fresh.source, lang: 'zh-TW', total: 4, done: 0 }]),
      ],
      untracked: world.listed ? [{ source: fresh.source, lang: 'zh-TW' }] : [],
    })
    answering(call => {
      requests += 1
      if (requests > 200) return { body: {}, after: new Promise<void>(() => undefined) }
      const path = call.path
      if (path.startsWith('/api/state')) {
        listings += 1
        const hold = over.listing?.(listings)
        // The body is built when the reply is released, so a test can change
        // the world while the request is in flight — which is what a terminal
        // does.
        if (hold) {
          const reply = { body: {} as StateResponse, after: hold.then(() => { reply.body = project() }) }
          return reply
        }
        return { body: project() }
      }
      if (path.startsWith('/api/models')) return { body: { provider: 'local', configured: 'qwen', models: [], error: null } }
      if (path.startsWith('/api/save')) {
        saves += 1
        // As the server answers by default: every target sent is stored, with a
        // new token — `stored` is what the page takes at once (contract,
        // `POST /api/save`), so a fixture answering it empty would be a server
        // this page never talks to.
        const sent = (call.body as { targets?: Record<string, string> } | null)?.targets ?? {}
        const stored = Object.fromEntries(Object.entries(sent).map(([id, text]) => [id, { text, token: `w-${id}-${saves}` }]))
        return over.save?.(saves, call.body) ?? { body: { applied: Object.keys(stored).length, unknown: [], stored, conflicts: {} } }
      }
      if (path.startsWith('/api/extract')) {
        over.onExtract?.()
        if ((call.body as { src?: string } | null)?.src === fresh.source) { world.made = true; world.listed = false }
        return { body: extracted }
      }
      if (path.startsWith('/api/hold')) { holds += 1; return over.hold?.(holds) ?? { body: { applied: 1, unknown: [] } } }
      if (path.startsWith('/api/job')) {
        return { body: { id: 'j1', done: true, total: 1, applied: 1, log: [], failures: [], refused: [], error: null, usage: { replies: 0, prompt_tokens: 0, completion_tokens: 0, reported: 0, unreported: 0 } } }
      }
      if (path.startsWith('/api/doc')) {
        const src = new URLSearchParams(path.split('?')[1]).get('src') ?? ''
        const n = (reads.get(src) ?? 0) + 1
        reads.set(src, n)
        const o = over.read?.(src, n)
        if (o) return o
        if (src === fresh.source) {
          return world.made
            ? { body: world.held }
            : { status: 400, body: { error: `no state for ${fresh.source} [zh-TW] — run \`lx extract ${fresh.source} --lang zh-TW\` first` } }
        }
        if (src === second.source) return { body: second }
        if (src === third.source) return { body: third }
        return { body: over.base ?? doc }
      }
      if (path.startsWith('/api/check')) return { body: { errors: 1, warnings: 0, by_rule: { missing: 1 } } }
      if (path.startsWith('/api/translate')) return { body: { id: 'j1', total: 0, route: { provider: 'local', model: 'qwen', error: null } } }
      if (path.startsWith('/api/sentences')) return { body: { sentences: [] } }
      if (path.startsWith('/api/preview')) {
        const blocks = fresh.segments.map(s => ({ id: s.id, kind: s.kind, from: 'source' as const, text: s.source }))
        return { body: { text: blocks.map(b => b.text).join(''), blocks, missing: 0, default_out: 'out.md' } }
      }
      return { body: { source: '', lang: 'zh-TW', tone: null, ids: [], voice: '', voice_notes: [], algorithm: 'none', cutoff: 0, records: 0, segments: [] } }
    })
  }

  const address = (src: string, seg?: string): string =>
    `#/doc/zh-TW/${encodeURIComponent(src)}` + (seg ? `?seg=${seg}` : '')

  const startAt = (hash: string): void => {
    window.location.hash = hash
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  }

  const settle = (ms = 50): Promise<void> => new Promise(r => { setTimeout(r, ms) })

  const opened = async (hash = address(doc.source, 's0003'), tally = '/3 translated'): Promise<void> => {
    startAt(hash)
    render(<App />)
    await waitFor(() => { expect(screen.getByText(tally)).toBeTruthy() }, { timeout: 4000 })
  }

  const held = (): { until: Promise<void>; release: () => void } => {
    let release: () => void = () => undefined
    const until = new Promise<void>(r => { release = r })
    return { until, release }
  }

  const unreachable = (): Answer => ({ body: {}, after: Promise.reject(new TypeError('Failed to fetch')) })

  const field = (): HTMLTextAreaElement => document.querySelector<HTMLTextAreaElement>('.ledger textarea')!
  const replaceIt = async (): Promise<void> => {
    await userEvent.setup().click(within(document.querySelector<HTMLDialogElement>('dialog[open]')!).getByRole('button', { name: 'Replace it' }))
  }
  const rowButton = (name: string): HTMLButtonElement =>
    [...document.querySelectorAll<HTMLButtonElement>('.ledger button')].find(b => b.textContent === name)!
  const entry = (): HTMLElement => screen.getByRole('button', { name: /docs\/untracked\.md.*extract/ })
  const offer = (): HTMLButtonElement => screen.getByRole('button', { name: `Extract ${fresh.source}` })
  const offered = (): boolean => screen.queryByRole('button', { name: `Extract ${fresh.source}` }) !== null
  const extracts = (): unknown[] => callsTo('/api/extract').map(c => c.body)
  const readsOf = (src: string): number =>
    callsTo('/api/doc').filter(c => c.path.includes(`src=${encodeURIComponent(src)}`)).length
  const logged = (level: string, text: string): boolean =>
    useStore.getState().log.some(l => l.level === level && l.text.includes(text))
  const dialogOpen = (): boolean => document.querySelector('dialog[open]') !== null

  const real = {
    showModal: Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'showModal'),
    close: Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'close'),
  }
  beforeEach(() => {
    world.made = false
    world.listed = true
    world.held = fresh
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
      configurable: true,
      value: function showModal(this: HTMLDialogElement) { this.setAttribute('open', '') },
    })
    Object.defineProperty(HTMLDialogElement.prototype, 'close', {
      configurable: true,
      value: function close(this: HTMLDialogElement) { this.removeAttribute('open') },
    })
  })
  afterEach(() => {
    for (const [name, d] of Object.entries(real)) {
      if (d) Object.defineProperty(HTMLDialogElement.prototype, name, d)
      else delete (HTMLDialogElement.prototype as unknown as Record<string, unknown>)[name]
    }
  })

  // ── the page for a file nobody has extracted ──────────────────────────────

  it('offers nothing over a read that never reached the server, and logs it', async () => {
    // A request that did not arrive says nothing about what the server holds.
    // The first version offered the extract over it and — because a listed
    // file's failure was not logged — left no line at all.
    serve({ read: (src, n) => (src === fresh.source && n === 1 ? unreachable() : null) })
    await opened()
    await userEvent.setup().click(entry())
    await waitFor(() => { expect(useStore.getState().docError).toContain('Failed to fetch') }, { timeout: 4000 })
    await settle()
    expect(offered()).toBe(false)
    expect(logged('bad', 'Failed to fetch')).toBe(true)
  }, 15000)

  it('offers nothing when a tracked document\'s read fails, whatever else is listed', async () => {
    // Both halves of the condition, and both fields of the listing: the file
    // must be the one listed, in the language listed, and a tracked chapter in
    // the same language whose read fails is not it.
    serve({ read: src => (src === second.source ? { status: 400, body: { error: 'database is locked' } } : null) })
    await opened()
    await userEvent.setup().click(screen.getByRole('button', { name: /book\/ch2\.md/ }))
    await waitFor(() => { expect(useStore.getState().docError).toBe('database is locked') }, { timeout: 4000 })
    await settle()
    expect(screen.queryByRole('button', { name: /^Extract / })).toBeNull()
    expect(logged('bad', 'database is locked')).toBe(true)
  }, 15000)

  it('asks the server again at the click, and reads the file rather than extract one a terminal has made', async () => {
    // `at` outlives a trip to the backend screens, so coming back reads nothing
    // and the offer still stands on the first read. A terminal extracted and
    // translated the file meanwhile; the click would have been an unconfirmed
    // re-extract of it. The server's list, read at the click, no longer names it.
    serve()
    await opened()
    await userEvent.setup().click(entry())
    await waitFor(() => { expect(offered()).toBe(true) }, { timeout: 4000 })
    window.location.hash = '#/backends'
    await settle()
    world.made = true
    world.listed = false
    world.held = freshTranslated
    window.history.back()
    await waitFor(() => { expect(offered()).toBe(true) }, { timeout: 4000 })

    await userEvent.setup().click(offer())
    await waitFor(() => { expect(screen.getByText('/4 translated')).toBeTruthy() }, { timeout: 4000 })
    await settle()
    expect(extracts()).toEqual([])
    expect(logged('warn', 'is no longer listed as not extracted')).toBe(true)
    expect(logged('warn', 'reading it again')).toBe(true)
    // All three ways a file stops being listed are named, since the page
    // cannot tell which it was.
    expect(logged('warn', 'shares its identity with another path')).toBe(true)
    expect(useStore.getState().log.some(l => l.text.startsWith('— extract'))).toBe(false)
  }, 15000)

  it('sends one extract and logs one header for two clicks in one frame', async () => {
    serve()
    await opened()
    await userEvent.setup().click(entry())
    await waitFor(() => { expect(offered()).toBe(true) }, { timeout: 4000 })

    // Both before React has redrawn the button disabled.
    act(() => { fireEvent.click(offer()); fireEvent.click(offer()) })
    await waitFor(() => { expect(screen.getByText('/4 translated')).toBeTruthy() }, { timeout: 4000 })
    await settle()
    expect(extracts()).toHaveLength(1)
    expect(useStore.getState().log.filter(l => l.text.startsWith('— extract'))).toHaveLength(1)
    // And the server was asked once for the two clicks: the bootstrap, the
    // click, and the reload after the extract. The second click is the same
    // act and says nothing.
    expect(callsTo('/api/state')).toHaveLength(3)
    expect(logged('warn', 'was not extracted')).toBe(false)
  }, 15000)

  it('offers the extract on the reading view\'s address too, and then shows the reading view', async () => {
    // One page for a file with no state, whichever view was asked for; once it
    // exists, the address's own view is drawn.
    serve()
    startAt(`#/read/zh-TW/${encodeURIComponent(fresh.source)}`)
    render(<App />)
    await waitFor(() => { expect(offered()).toBe(true) }, { timeout: 4000 })
    await userEvent.setup().click(offer())
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Back to the ledger/ })).toBeTruthy()
    }, { timeout: 4000 })
    expect(extracts()).toEqual([{ src: fresh.source, lang: 'zh-TW' }])
  }, 15000)

  it('offers nothing on a refusal to leave, even after an earlier visit left a failed read behind', async () => {
    // `readFailed` is lowered at the start of every open. Left up from the visit
    // to the file's page, it drew the offer over the later refusal — a page
    // whose trouble is the chapter's unwritten words.
    serve({ save: () => refused })
    await opened()
    const user = userEvent.setup()
    await user.click(entry())
    await waitFor(() => { expect(offered()).toBe(true) }, { timeout: 4000 })
    await user.click(screen.getByRole('button', { name: /book\/ch1\.md/ }))
    await waitFor(() => { expect(screen.getByText('/3 translated')).toBeTruthy() }, { timeout: 4000 })

    await user.type(field(), '燈還亮著。')
    await user.click(entry())
    await waitFor(() => { expect(screen.getByText(/could not be written to book\/ch1\.md/)).toBeTruthy() }, { timeout: 4000 })
    await settle()
    expect(offered()).toBe(false)
    expect(drafts.get('s0003')).toBe('燈還亮著。')
  }, 15000)

  // ── the document on screen is the one the page chose ─────────────────────

  it('never lets a late refresh put a document on screen that the page declined to show (older)', async () => {
    // Chapter one's refresh is still in flight across a whole visit to chapter
    // two, where the reviewer types and whose flush then fails, so the page
    // declines to go back. The refresh used to be accepted because `at` named
    // chapter one — putting chapter one under chapter two's words — and the
    // save the refusal itself recommends then posted them onto chapter one's
    // heading, with chapter one's token.
    const refresh = held()
    serve({
      save: n => (n === 1 ? refused : null),
      read: (src, n) => (src === doc.source && n === 2 ? { body: doc, after: refresh.until } : null),
    })
    await opened()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Check' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(screen.getByText('/2 translated')).toBeTruthy() }, { timeout: 4000 })
    await user.type(field(), '早晨來得晚。')
    window.location.hash = address(doc.source, 's0003')
    await waitFor(() => { expect(screen.getByText(/could not be written to book\/ch2\.md/)).toBeTruthy() }, { timeout: 4000 })

    await act(async () => { refresh.release(); await settle(100) })
    expect(useStore.getState().doc?.source).toBe(second.source)

    // What the refusal tells the reviewer to do.
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(callsTo('/api/save').length).toBeGreaterThan(1) }, { timeout: 4000 })
    await settle(100)
    for (const c of callsTo('/api/save')) expect((c.body as { src: string }).src).toBe(second.source)
    expect((callsTo('/api/save').at(-1)!.body as { targets: Record<string, string> }).targets)
      .toEqual({ s0001: '早晨來得晚。' })
  }, 15000)

  it('does not re-extract, or strand words, on the strength of that late refresh (older)', async () => {
    // The same late refresh, through Re-extract: the post-refresh check passed
    // because `doc` had become chapter one again, and the extract stranded the
    // second chapter's words — which nothing re-parsed — as "renumbered".
    const refresh = held()
    serve({
      save: n => (n === 1 ? refused : null),
      read: (src, n) => (src === doc.source && n === 2 ? { body: doc, after: refresh.until } : null),
    })
    await opened()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(screen.getByText('/2 translated')).toBeTruthy() }, { timeout: 4000 })
    await user.type(field(), '早晨來得晚。')
    window.location.hash = address(doc.source, 's0003')
    await waitFor(() => { expect(screen.getByText(/could not be written to book\/ch2\.md/)).toBeTruthy() }, { timeout: 4000 })

    await act(async () => { refresh.release(); await settle(200) })
    expect(dialogOpen()).toBe(false)
    expect(extracts()).toEqual([])
    expect(drafts.get('s0001')).toBe('早晨來得晚。')
    expect(drafts.stranded()).toBe(false)
    expect(logged('warn', 'was not re-extracted')).toBe(true)
  }, 15000)

  it('does not leave the page on the loading screen when an open is overtaken during its flush (older)', async () => {
    // Back, Back with words unsaved: the first open was still flushing when the
    // second finished, and carried on — raising `docLoading` over the page the
    // second had drawn and never lowering it.
    const first = held()
    serve({
      save: n => (n === 1
        ? { body: wrote, after: first.until }
        : { body: { applied: 0, unknown: [], stored: {}, conflicts: { s0003: { text: '燈還亮著。', token: 'z' } } } }),
    })
    await opened()
    await userEvent.setup().type(field(), '燈還亮著。')
    window.location.hash = address(second.source)
    await waitFor(() => { expect(callsTo('/api/save')).toHaveLength(1) })
    window.location.hash = address(third.source)
    await waitFor(() => { expect(readsOf(third.source)).toBe(1) }, { timeout: 4000 })
    await waitFor(() => { expect(screen.getByText('/2 translated')).toBeTruthy() }, { timeout: 4000 })

    await act(async () => { first.release(); await settle(100) })
    expect(screen.queryByText(/^reading /)).toBeNull()
    expect(useStore.getState().doc?.source).toBe(third.source)
    expect(readsOf(second.source)).toBe(0)
  }, 15000)

  // ── the toolbar's confirmation, decided from what it has just read ────────

  it('does not decide Re-extract\'s confirmation from a snapshot it could not re-read (older)', async () => {
    // The re-read exists because the snapshot can be stale — `lx run` may have
    // translated the book in a terminal. When the re-read failed, the snapshot
    // decided anyway, and one saying nothing was translated skipped the dialog.
    serve({ base: untranslated, read: (src, n) => (src === doc.source && n === 2 ? unreachable() : null) })
    await opened(address(doc.source), '/3 translated')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Re-extract' }))
    await settle(200)
    expect(dialogOpen()).toBe(false)
    expect(extracts()).toEqual([])
    expect(logged('warn', 'could not be read again')).toBe(true)
  }, 15000)

  it('does not carry Re-extract on when the address has moved to a file whose read failed (older)', async () => {
    // Such a move leaves `doc` on the chapter, so comparing the screen alone
    // let the dialog open over the other file's page.
    const refresh = held()
    serve({ read: (src, n) => (src === doc.source && n === 2 ? { body: doc, after: refresh.until } : null) })
    await opened()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    window.location.hash = address(fresh.source)
    await waitFor(() => { expect(screen.getByRole('heading', { name: fresh.source })).toBeTruthy() }, { timeout: 4000 })
    await act(async () => { refresh.release(); await settle(200) })
    expect(dialogOpen()).toBe(false)
    expect(extracts()).toEqual([])
    // The page moved: that is the reason given, not a failed read.
    expect(logged('warn', 'the page moved to another document')).toBe(true)
    expect(logged('warn', 'could not be read again')).toBe(false)
  }, 15000)

  it('does not log Start over\'s header over an extract a run has started in front of', async () => {
    serve()
    await opened()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /Start over in another register/ }))
    await user.type(screen.getByPlaceholderText('type a register'), 'plain')
    await user.click(screen.getByRole('button', { name: 'Start over…' }))
    await waitFor(() => { expect(dialogOpen()).toBe(true) }, { timeout: 4000 })
    act(() => { useStore.setState({ running: true }) })
    await user.click(within(document.querySelector<HTMLDialogElement>('dialog[open]')!).getByRole('button', { name: 'Discard and re-extract' }))
    await settle(100)
    expect(extracts()).toEqual([])
    expect(useStore.getState().log.some(l => l.text.startsWith('— start over'))).toBe(false)
    expect(logged('warn', 'a run started')).toBe(true)
  }, 15000)

  // ── an open asked for again, and the clicks that ask the server ──────────
  //
  // The third review. `open()` told its own calls apart by the document `at`
  // named, and the same document can be asked for twice while the first call
  // is in flight — A, B, C, back to B.

  it('does not let a slow first open of a document discard words a second open of it declined to leave (older)', async () => {
    const slow = held()
    serve({ read: (src, n) => (src === second.source && n === 1 ? { body: second, after: slow.until } : null), save: () => refused })
    await opened()
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(readsOf(second.source)).toBe(1) })
    window.location.hash = address(third.source, 's0001')
    await waitFor(() => { expect(useStore.getState().doc?.source).toBe(third.source) }, { timeout: 4000 })
    await userEvent.setup().type(field(), '早晨來得晚。')
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(screen.getByText(/could not be written to book\/ch3\.md/)).toBeTruthy() }, { timeout: 4000 })

    // The first open's read lands: it was overtaken, and does nothing.
    await act(async () => { slow.release(); await settle(150) })
    expect(drafts.get('s0001')).toBe('早晨來得晚。')
    expect(useStore.getState().doc?.source).toBe(third.source)
    expect(screen.getByText(/could not be written to book\/ch3\.md/)).toBeTruthy()
  }, 20000)

  it('does not let a late failed read of the file replace a refusal to leave, or offer the extract over it', async () => {
    const slow = held()
    serve({
      read: (src, n) => (src === fresh.source && n === 1
        ? { status: 400, body: { error: `no state for ${fresh.source} [zh-TW]` }, after: slow.until }
        : null),
      save: () => refused,
    })
    await opened()
    const user = userEvent.setup()
    await user.click(entry())
    await waitFor(() => { expect(readsOf(fresh.source)).toBe(1) })
    await user.click(screen.getByRole('button', { name: /book\/ch1\.md/ }))
    await waitFor(() => { expect(screen.getByText('/3 translated')).toBeTruthy() }, { timeout: 4000 })
    await user.type(field(), '燈還亮著。')
    await user.click(entry())
    await waitFor(() => { expect(screen.getByText(/could not be written to book\/ch1\.md/)).toBeTruthy() }, { timeout: 4000 })

    await act(async () => { slow.release(); await settle(150) })
    expect(offered()).toBe(false)
    expect(useStore.getState().docError).toMatch(/^s0003 could not be written to book\/ch1\.md/)
    expect(extracts()).toEqual([])
  }, 20000)

  it('does not let an overtaken open write its refusal over the page of a later open of the same document (older)', async () => {
    const s1 = held()
    const s3 = held()
    serve({ save: n => (n === 1 ? { body: wrote, after: s1.until } : n === 3 ? { body: wrote, after: s3.until } : null) })
    await opened()
    const user = userEvent.setup()
    await user.type(field(), '燈還亮著。')
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(callsTo('/api/save')).toHaveLength(1) })
    window.location.hash = address(third.source, 's0001')
    await waitFor(() => { expect(useStore.getState().doc?.source).toBe(third.source) }, { timeout: 4000 })
    await user.type(field(), '早晨來得晚。')
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(callsTo('/api/save')).toHaveLength(3) })

    await act(async () => { s1.release(); await settle(100) })
    await act(async () => { s3.release(); await settle(200) })
    await waitFor(() => { expect(useStore.getState().doc?.source).toBe(second.source) }, { timeout: 4000 })
    expect(useStore.getState().docError).toBe('')
  }, 20000)

  it('keeps words typed during the re-read Re-extract makes, and does not extract over them (older)', async () => {
    // The ledger stays editable through the re-read. Words typed then used to
    // be stranded by the extract and named as gone.
    const refresh = held()
    serve({ base: untranslated, read: (src, n) => (src === doc.source && n === 2 ? { body: untranslated, after: refresh.until } : null) })
    await opened(address(doc.source, 's0003'), '/3 translated')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await user.type(field(), '燈還亮著。')
    await act(async () => { refresh.release(); await settle(300) })

    expect(extracts()).toEqual([])
    expect(dialogOpen()).toBe(false)
    expect(drafts.get('s0003')).toBe('燈還亮著。')
    expect(logged('bad', 'are gone')).toBe(false)
    expect(logged('warn', 'wording changed while it was being read')).toBe(true)
  }, 20000)

  it('says why when a run starts while the click is asking the server', async () => {
    const listing = held()
    serve({ listing: n => (n === 2 ? listing.until : null) })
    await opened()
    await userEvent.setup().click(entry())
    await waitFor(() => { expect(offered()).toBe(true) }, { timeout: 4000 })
    fireEvent.click(offer())
    await waitFor(() => { expect(callsTo('/api/state')).toHaveLength(2) })
    act(() => { useStore.setState({ running: true }) })
    await act(async () => { listing.release(); await settle(150) })

    expect(extracts()).toEqual([])
    expect(logged('warn', `${fresh.source} was not extracted: something else started running`)).toBe(true)
    expect(useStore.getState().log.some(l => l.text.startsWith('— extract'))).toBe(false)
  }, 20000)

  it('reads nothing and says so when the address moved while the click was asking', async () => {
    const listing = held()
    serve({ listing: n => (n === 2 ? listing.until : null) })
    await opened()
    await userEvent.setup().click(entry())
    await waitFor(() => { expect(offered()).toBe(true) }, { timeout: 4000 })
    fireEvent.click(offer())
    await waitFor(() => { expect(callsTo('/api/state')).toHaveLength(2) })
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(useStore.getState().doc?.source).toBe(second.source) }, { timeout: 4000 })
    // A terminal extracts the file while the click's question is in flight.
    world.made = true
    world.listed = false
    world.held = freshTranslated
    await act(async () => { listing.release(); await settle(150) })

    expect(extracts()).toEqual([])
    expect(useStore.getState().doc?.source).toBe(second.source)
    expect(readsOf(fresh.source)).toBe(1)
    expect(logged('warn', 'open it from the rail')).toBe(true)
    expect(logged('warn', 'reading it again')).toBe(false)
  }, 20000)

  it('does not carry Re-extract on when the address moved to the same file in another language', async () => {
    // Each clause of "is this still the document the dialog would name" is
    // needed: here only the language `at` names says the page has moved.
    const refresh = held()
    serve({
      read: (src, n) => (src === doc.source && n === 2 ? { body: doc, after: refresh.until }
        : src === doc.source && n === 3 ? { status: 400, body: { error: 'no state for book/ch1.md [ja]' } }
          : null),
    })
    await opened()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    window.location.hash = `#/doc/ja/${encodeURIComponent(doc.source)}`
    await waitFor(() => { expect(readsOf(doc.source)).toBe(3) })
    await act(async () => { refresh.release(); await settle(200) })
    expect(dialogOpen()).toBe(false)
    expect(extracts()).toEqual([])
    expect(logged('warn', 'the page moved to another document')).toBe(true)
  }, 20000)

  // ── the fourth review: the count a confirmation reads, and whose click it is ──

  const withWords: DocResponse = {
    ...untranslated,
    report: { ...untranslated.report, translated: 1 },
    segments: untranslated.segments.map(s => (s.id === 's0003'
      ? { ...s, target: '燈還亮著。', status: 'translated' as const, origin: 'human', token: 'w3' }
      : s)),
  }

  it('asks before re-extracting a document its re-read shows translated, whatever the page last drew', async () => {
    // The page drew the chapter untranslated; `lx run` has since translated it.
    // The dialog is decided from the re-read, not from what was on screen.
    serve({ base: untranslated, read: (src, n) => (src === doc.source && n === 2 ? { body: doc } : null) })
    await opened(address(doc.source), '/3 translated')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(dialogOpen()).toBe(true) }, { timeout: 4000 })
    expect(extracts()).toEqual([])
  }, 20000)

  it('does not re-extract when a blur wrote during its re-read, whichever read lands first', async () => {
    // The count can be older than the reviewer's own write however the reads
    // come back — this ordering, the re-read landing before the save's own,
    // reopened the defect the previous repair of the ordering had closed. So
    // it asks whether anything was written, not which read is newer.
    const refresh = held()
    const saveRead = held()
    let saved = false
    serve({
      base: untranslated,
      save: () => { saved = true; return null },
      read: (src, n) => (src === doc.source && n === 2 ? { body: untranslated, after: refresh.until }
        : src === doc.source && n === 3 ? { body: withWords, after: saveRead.until }
          : src === doc.source && saved ? { body: withWords }
            : null),
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await user.type(field(), '燈還亮著。')
    await user.click(screen.getByText('The lamp was still burning.'))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(3) })
    await act(async () => { refresh.release(); await settle(300) })
    const sent = extracts()
    const asked = dialogOpen()
    await act(async () => { saveRead.release(); await settle(200) })

    expect(sent).toEqual([])
    expect(asked).toBe(false)
    expect(logged('warn', 'wording changed while it was being read')).toBe(true)
  }, 20000)

  it('does not re-extract when a blur wrote during its re-read and the save\'s own re-read failed', async () => {
    const refresh = held()
    let saved = false
    serve({
      base: untranslated,
      // As the server answers: what it stored, and the new token.
      save: () => { saved = true; return { body: { applied: 1, unknown: [], stored: { s0003: { text: '燈還亮著。', token: 'w3' } }, conflicts: {} } } },
      read: (src, n) => (src === doc.source && n === 2 ? { body: untranslated, after: refresh.until }
        : src === doc.source && n === 3 ? unreachable()
          : src === doc.source && saved ? { body: withWords }
            : null),
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await user.type(field(), '燈還亮著。')
    await user.click(screen.getByText('The lamp was still burning.'))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(3) })
    await act(async () => { refresh.release(); await settle(300) })

    expect(extracts()).toEqual([])
    expect(dialogOpen()).toBe(false)
  }, 20000)

  it('does not decide Re-extract\'s confirmation from a re-read older than the page\'s own later read (older)', async () => {
    // Left and came back while the re-read was in flight, the way out writing
    // the words that were typed: the count the re-read carried predates them.
    const refresh = held()
    let saved = false
    serve({
      base: untranslated,
      save: () => { saved = true; return null },
      read: (src, n) => (src === doc.source && n === 2 ? { body: untranslated, after: refresh.until }
        : src === doc.source && saved ? { body: withWords }
          : null),
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await user.type(field(), '燈還亮著。')
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(callsTo('/api/save')).toHaveLength(1) }, { timeout: 4000 })
    await waitFor(() => { expect(useStore.getState().doc?.source).toBe(second.source) }, { timeout: 4000 })
    window.location.hash = address(doc.source, 's0003')
    await waitFor(() => { expect(useStore.getState().doc?.report.translated).toBe(1) }, { timeout: 4000 })

    await act(async () => { refresh.release(); await settle(300) })
    // The confirmation's half. That the older re-read also lands over the newer
    // count on screen is HANDOFF-096's, and is pinned by the known-broken test
    // "keeps wording a blur has just written on screen when an older re-read lands".
    expect(extracts()).toEqual([])
    expect(logged('warn', 'wording changed while it was being read')).toBe(true)
  }, 20000)

  it('says words that changed during its own save changed, not that they could not be saved', async () => {
    // `save()` answered that everything it sent was written; what is left was
    // edited while it was in flight, which is not a refusal.
    const first = held()
    serve({ base: untranslated, save: n => (n === 1 ? { body: wrote, after: first.until } : null) })
    await opened(address(doc.source, 's0003'), '/3 translated')
    act(() => { drafts.set('s0003', '燈還亮著。', '') })
    await userEvent.setup().click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(callsTo('/api/save')).toHaveLength(1) })
    act(() => { drafts.set('s0002', '另一段。', '') })
    await act(async () => { first.release(); await settle(200) })

    expect(extracts()).toEqual([])
    expect(logged('bad', 'could not be saved')).toBe(false)
    expect(logged('warn', 'changed while this was saving')).toBe(true)
  }, 20000)

  it('says the file needs no extract rather than that something is running, when both are true', async () => {
    // Which reason is given is decided: a file that no longer needs extracting
    // has nothing to wait for.
    const listing = held()
    serve({ listing: n => (n === 2 ? listing.until : null) })
    await opened()
    await userEvent.setup().click(entry())
    await waitFor(() => { expect(offered()).toBe(true) }, { timeout: 4000 })
    fireEvent.click(offer())
    await waitFor(() => { expect(callsTo('/api/state')).toHaveLength(2) })
    act(() => { useStore.setState({ running: true }) })
    world.made = true
    world.listed = false
    world.held = freshTranslated
    await act(async () => { listing.release(); await settle(150) })

    expect(extracts()).toEqual([])
    expect(logged('warn', 'is no longer listed as not extracted')).toBe(true)
    expect(logged('warn', 'something else started running')).toBe(false)
  }, 20000)

  // ── the fifth review: which read is the latest, and the last look ────────

  it('takes a last look after its dialog, and does not extract over wording that changed under it', async () => {
    // Nothing can be typed under a modal dialog, but a save can still land or
    // the map still change; the look after it is the one nothing is awaited
    // behind.
    serve()
    await opened(address(doc.source, 's0003'), '/3 translated')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(dialogOpen()).toBe(true) }, { timeout: 4000 })
    act(() => { drafts.set('s0002', '另一段。', '她沒有睡。') })
    await userEvent.setup().click(within(document.querySelector<HTMLDialogElement>('dialog[open]')!).getByRole('button', { name: 'Re-extract' }))
    await settle(200)

    expect(extracts()).toEqual([])
    expect(logged('warn', 'wording changed while this was asking')).toBe(true)
  }, 20000)

  it('asks before re-extracting over a translation a blur wrote while its re-read was in flight (older)', async () => {
    // The re-read was asked before the blur's save and answered after the
    // save's own re-read, and put the count from before the save back.
    const refresh = held()
    let saved = false
    serve({
      base: untranslated,
      save: () => { saved = true; return null },
      read: (src, n) => (src === doc.source && n === 2 ? { body: untranslated, after: refresh.until }
        : src === doc.source && saved ? { body: withWords }
          : null),
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await user.type(field(), '燈還亮著。')
    await user.click(screen.getByText('The lamp was still burning.'))
    await waitFor(() => { expect(useStore.getState().doc?.report.translated).toBe(1) }, { timeout: 4000 })
    await act(async () => { refresh.release(); await settle(300) })

    // The confirmation's half; the count on screen is HANDOFF-096's (see above).
    expect(extracts()).toEqual([])
    expect(logged('warn', 'wording changed while it was being read')).toBe(true)
  }, 20000)

  // **Known broken, HANDOFF-096.** The page does not order its reads of a
  // document, so an older one can land over a newer one, and a save's re-read
  // is the only way the page learns what the save stored. Written by
  // HANDOFF-088's reviews against defects that fail on `83a865b` too, and
  // repaired there four ways that each opened another ordering; kept here as
  // `it.fails`, so the suite turns red the day the design lands and the marker
  // is removed in the same commit — the convention `KNOWN_BROKEN` follows in
  // `tests/test_pipeline.py`.
  it.fails('keeps wording a blur has just written on screen when an older re-read lands (older)', async () => {
    const refresh = held()
    let saved = false
    const written: DocResponse = {
      ...doc,
      report: { ...doc.report, translated: 3, errors: 0, by_rule: {} },
      segments: doc.segments.map(s => (s.id === 's0003'
        ? { ...s, target: '燈還亮著。', status: 'translated' as const, origin: 'human', token: 'w3', issues: [] }
        : s)),
    }
    serve({
      save: () => { saved = true; return null },
      read: (src, n) => (src === doc.source && n === 2 ? { body: doc, after: refresh.until }
        : src === doc.source && saved ? { body: written }
          : null),
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Check' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await user.type(field(), '燈還亮著。')
    await user.click(screen.getByText('The lamp was still burning.'))
    await waitFor(() => { expect(useStore.getState().doc?.report.translated).toBe(3) }, { timeout: 4000 })
    await act(async () => { refresh.release(); await settle(300) })

    expect(useStore.getState().doc?.segments.find(s => s.id === 's0003')?.target).toBe('燈還亮著。')
    expect(field().value).toBe('燈還亮著。')
  }, 20000)

  it('does not send "Draft again" built from a document the page is about to replace', async () => {
    // Back and Forward during its save: the save's re-read was discarded for the
    // newer read on the way back, and the origin read from what was left
    // predated the save that made the segment a person's — no question asked,
    // the model billed, the write refused.
    const refresh = held()
    const backRead = held()
    const away = held()
    let saved = false
    serve({
      save: () => { saved = true; return null },
      read: (src, n) => (src === doc.source && n === 2 ? { body: withWords, after: refresh.until }
        : src === doc.source && n === 3 ? { body: withWords, after: backRead.until }
          : src === second.source && n === 1 ? { body: second, after: away.until }
            : src === doc.source && saved ? { body: withWords }
              : null),
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    await userEvent.setup().type(field(), '燈還亮著。')
    act(() => { [...document.querySelectorAll<HTMLButtonElement>('.ledger button')].find(b => b.textContent === 'Draft again')!.click() })
    await waitFor(() => { expect(saved).toBe(true) })
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(readsOf(second.source)).toBe(1) })
    window.location.hash = address(doc.source, 's0003')
    await waitFor(() => { expect(readsOf(doc.source)).toBe(3) })
    await act(async () => { refresh.release(); await settle(200) })

    expect(callsTo('/api/translate')).toHaveLength(0)
    expect(logged('warn', 's0003 was not sent: the document on screen was being replaced')).toBe(true)
    await act(async () => { backRead.release(); away.release(); await settle(100) })
  }, 20000)

  // ── the sixth review: a read that landed, not one that was asked ─────────
  //
  // The fifth round's repair dropped a read whenever a later one had been
  // *asked*, and a later one that failed or was still on its way then cost the
  // page the save's good re-read. Ported from that review's probes.

  it('asks before "Draft again" over wording a blur wrote, when a later re-read fails', async () => {
    const saveRead = held()
    let saved = false
    serve({
      base: untranslated,
      save: () => { saved = true; return null },
      read: (src, n) => (src === doc.source && n === 2 ? { body: withWords, after: saveRead.until }
        : src === doc.source && n === 3 ? unreachable()
          : src === doc.source && saved ? { body: withWords }
            : null),
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    const user = userEvent.setup()
    await user.type(field(), '燈還亮著。')
    await user.click(screen.getByText('The lamp was still burning.'))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await user.click(screen.getByRole('button', { name: 'Check' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(3) })
    await act(async () => { saveRead.release(); await settle(200) })

    // What the page now shows for the segment a person has just written.
    const shown = useStore.getState().doc?.segments.find(s => s.id === 's0003')
    await user.click(rowButton('Draft again'))
    await settle(300)

    expect(callsTo('/api/translate')).toHaveLength(0)
    expect(dialogOpen()).toBe(true)
    expect({ origin: shown?.origin, translated: useStore.getState().doc?.report.translated })
      .toEqual({ origin: 'human', translated: 1 })
  }, 20000)

  // P1b: the same, with the later re-read merely still in flight. settled() says
  // true while "a read is on its way to replace the document on screen".
  it('does not send "Draft again" from a snapshot while a later re-read is still on its way', async () => {
    const saveRead = held()
    const checkRead = held()
    let saved = false
    serve({
      base: untranslated,
      save: () => { saved = true; return null },
      read: (src, n) => (src === doc.source && n === 2 ? { body: withWords, after: saveRead.until }
        : src === doc.source && n === 3 ? { body: withWords, after: checkRead.until }
          : src === doc.source && saved ? { body: withWords }
            : null),
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    const user = userEvent.setup()
    await user.type(field(), '燈還亮著。')
    await user.click(screen.getByText('The lamp was still burning.'))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await user.click(screen.getByRole('button', { name: 'Check' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(3) })
    await act(async () => { saveRead.release(); await settle(200) })

    await user.click(rowButton('Draft again'))
    await settle(300)
    const sent = callsTo('/api/translate').length
    await act(async () => { checkRead.release(); await settle(100) })

    expect(sent).toBe(0)
  }, 20000)

  // P2: open()'s own landing ignores readSeq. Back and Forward while Hold is in
  // flight: the open on the way back reads before the hold is written, the
  // hold's re-read reads after and lands first, and the open's older read then
  // lands over it. Nothing reads again.
  // **Known broken, HANDOFF-096.** The page does not order its reads of a
  // document, so an older one can land over a newer one, and a save's re-read
  // is the only way the page learns what the save stored. Written by
  // HANDOFF-088's reviews against defects that fail on `83a865b` too, and
  // repaired there four ways that each opened another ordering; kept here as
  // `it.fails`, so the suite turns red the day the design lands and the marker
  // is removed in the same commit — the convention `KNOWN_BROKEN` follows in
  // `tests/test_pipeline.py`.
  it.fails('does not let an open\'s older read land over a later re-read (hold) (older)', async () => {
    const holdGate = held()
    const backRead = held()
    const away = held()
    let holdDone = false
    const heldDoc: DocResponse = {
      ...doc,
      segments: doc.segments.map(s => (s.id === 's0001' ? { ...s, review: 'held' as const, token: 't1h' } : s)),
    }
    serve({
      hold: () => ({ body: { applied: 1, unknown: [] }, after: holdGate.until.then(() => { holdDone = true }) }),
      read: (src, n) => (src === doc.source && n === 2 ? { body: doc, after: backRead.until }
        : src === second.source && n === 1 ? { body: second, after: away.until }
          : src === doc.source && holdDone ? { body: heldDoc }
            : null),
    })
    await opened(address(doc.source, 's0001'), '/3 translated')
    act(() => { rowButton('Hold').click() })
    await waitFor(() => { expect(callsTo('/api/hold')).toHaveLength(1) })
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(readsOf(second.source)).toBe(1) })
    window.location.hash = address(doc.source, 's0001')
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await act(async () => { holdGate.release(); await settle(200) })
    await waitFor(() => { expect(readsOf(doc.source)).toBe(3) })
    await act(async () => { backRead.release(); await settle(200) })
    await act(async () => { away.release(); await settle(100) })

    expect(useStore.getState().doc?.segments.find(s => s.id === 's0001')?.review).toBe('held')
  }, 20000)

  // P1c: the same dropped re-read, read by the one dialog that discards a
  // document: it states the count from before the save.
  it('states the translated count a blur has just changed, in Start over\'s dialog', async () => {
    const saveRead = held()
    let saved = false
    serve({
      base: untranslated,
      save: () => { saved = true; return null },
      read: (src, n) => (src === doc.source && n === 2 ? { body: withWords, after: saveRead.until }
        : src === doc.source && n === 3 ? unreachable()
          : src === doc.source && saved ? { body: withWords }
            : null),
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    const user = userEvent.setup()
    await user.type(field(), '燈還亮著。')
    await user.click(screen.getByText('The lamp was still burning.'))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await user.click(screen.getByRole('button', { name: 'Check' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(3) })
    await act(async () => { saveRead.release(); await settle(200) })

    await user.click(screen.getByRole('button', { name: /Start over in another register/ }))
    await user.type(screen.getByPlaceholderText('type a register'), 'plain')
    await user.click(screen.getByRole('button', { name: 'Start over…' }))
    await waitFor(() => { expect(dialogOpen()).toBe(true) })
    expect(document.querySelector('dialog[open]')!.textContent).toContain('1 of 3 segments are translated now')
  }, 20000)

  // P1e: the reviewer's own next edit is refused as a conflict with their own
  // first save, because the token the page holds predates it — and the edit is
  // then replaced on screen by the first wording.
  it('bases the next edit on the token the page\'s own save produced', async () => {
    const saveRead = held()
    let saved = false
    serve({
      base: untranslated,
      save: (_n, body) => {
        const base = (body as { base?: Record<string, string> }).base ?? {}
        if (saved && base['s0003'] !== 'w3') {
          return { body: { applied: 0, unknown: [], stored: {}, conflicts: { s0003: { target: '燈還亮著。', token: 'w3' } } } }
        }
        saved = true
        return null
      },
      read: (src, n) => (src === doc.source && n === 2 ? { body: withWords, after: saveRead.until }
        : src === doc.source && n === 3 ? unreachable()
          : src === doc.source && saved ? { body: withWords }
            : null),
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    const user = userEvent.setup()
    await user.type(field(), '燈還亮著。')
    await user.click(screen.getByText('The lamp was still burning.'))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await user.click(screen.getByRole('button', { name: 'Check' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(3) })
    await act(async () => { saveRead.release(); await settle(200) })

    await user.type(field(), '還')
    await user.click(screen.getByText('The lamp was still burning.'))
    await waitFor(() => { expect(callsTo('/api/save')).toHaveLength(2) })
    await settle(300)

    expect(logged('bad', 'changed underneath this edit')).toBe(false)
    expect((callsTo('/api/save')[1]!.body as { base: Record<string, string> }).base).toEqual({ s0003: 'w3' })
  }, 20000)

  // P2b: P2's stale landing, acted on: "Draft again" on a segment the reviewer
  // has just held is sent without the held-segment question.
  // **Known broken, HANDOFF-096.** The page does not order its reads of a
  // document, so an older one can land over a newer one, and a save's re-read
  // is the only way the page learns what the save stored. Written by
  // HANDOFF-088's reviews against defects that fail on `83a865b` too, and
  // repaired there four ways that each opened another ordering; kept here as
  // `it.fails`, so the suite turns red the day the design lands and the marker
  // is removed in the same commit — the convention `KNOWN_BROKEN` follows in
  // `tests/test_pipeline.py`.
  it.fails('asks before sending a segment just held, after Back and Forward during the hold (older)', async () => {
    const holdGate = held()
    const backRead = held()
    const away = held()
    let holdDone = false
    const unheld: DocResponse = {
      ...doc,
      segments: doc.segments.map(s => (s.id === 's0002' ? { ...s, review: null } : s)),
    }
    const heldDoc: DocResponse = {
      ...doc,
      segments: doc.segments.map(s => (s.id === 's0002' ? { ...s, review: 'held' as const } : s)),
    }
    serve({
      base: unheld,
      hold: () => ({ body: { applied: 1, unknown: [] }, after: holdGate.until.then(() => { holdDone = true }) }),
      read: (src, n) => (src === doc.source && n === 2 ? { body: unheld, after: backRead.until }
        : src === second.source && n === 1 ? { body: second, after: away.until }
          : src === doc.source && holdDone ? { body: heldDoc }
            : null),
    })
    await opened(address(doc.source, 's0002'), '/3 translated')
    act(() => { rowButton('Hold').click() })
    await waitFor(() => { expect(callsTo('/api/hold')).toHaveLength(1) })
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(readsOf(second.source)).toBe(1) })
    window.location.hash = address(doc.source, 's0002')
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await act(async () => { holdGate.release(); await settle(200) })
    await waitFor(() => { expect(readsOf(doc.source)).toBe(3) })
    await act(async () => { backRead.release(); await settle(200) })
    await act(async () => { away.release(); await settle(100) })

    act(() => { rowButton('Draft again').click() })
    await settle(300)
    expect(callsTo('/api/translate')).toHaveLength(0)
    expect(dialogOpen()).toBe(true)
  }, 20000)

  // P4: an unrelated read asked during Re-extract's own re-read (Check pressed
  // while it reads) makes Re-extract refuse, saying the document "changed while
  // it was being read" although nothing was written.
  it('carries Re-extract on when a Check read was asked during its re-read', async () => {
    const tRead = held()
    serve({
      read: (src, n) => (src === doc.source && n === 2 ? { body: doc, after: tRead.until } : null),
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await user.click(screen.getByRole('button', { name: 'Check' }))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(3) })
    await settle(100)
    await act(async () => { tRead.release(); await settle(300) })

    expect(logged('warn', 'changed while it was being read')).toBe(false)
    expect(dialogOpen()).toBe(true)
  }, 20000)

  // P5: a confirmed "Replace it" is dropped without a line when Back and
  // Forward happen under the dialog: runJob's settled() is false while the open
  // on the way back is reading, and runJob returns silently.
  it('sends, or says why not, a "Draft again" the reviewer confirmed while Back and Forward read the page again', async () => {
    const backRead = held()
    const away = held()
    serve({
      read: (src, n) => (src === doc.source && n === 2 ? { body: doc, after: backRead.until }
        : src === second.source && n === 1 ? { body: second, after: away.until }
          : null),
    })
    await opened(address(doc.source, 's0001'), '/3 translated')
    act(() => { rowButton('Draft again').click() })
    await waitFor(() => { expect(dialogOpen()).toBe(true) })
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(readsOf(second.source)).toBe(1) })
    window.location.hash = address(doc.source, 's0001')
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await userEvent.setup().click(within(document.querySelector<HTMLDialogElement>('dialog[open]')!).getByRole('button', { name: 'Replace it' }))
    await settle(300)
    const sent = callsTo('/api/translate').length
    const said = useStore.getState().log.filter(l => l.text.includes('s0001')).map(l => l.text)
    await act(async () => { backRead.release(); away.release(); await settle(100) })

    expect({ sent, said }).not.toEqual({ sent: 0, said: [] })
  }, 20000)

  // P6: "Draft again" overtaken by the toolbar's Re-extract (pressed while its
  // save was in flight): the refusal names a move that did not happen.
  it('does not say the page moved when what overtook "Draft again" was a re-extract of the same page', async () => {
    const firstSave = held()
    const reopen = held()
    let extracted = false
    let after = 0
    serve({
      base: untranslated,
      onExtract: () => { extracted = true },
      save: n => (n === 1 ? { body: wrote, after: firstSave.until } : null),
      read: src => {
        if (src !== doc.source || !extracted) return null
        after += 1
        return after === 1 ? { body: untranslated, after: reopen.until } : null
      },
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    await userEvent.setup().type(field(), '燈')
    act(() => { rowButton('Draft again').click() })
    await waitFor(() => { expect(callsTo('/api/save')).toHaveLength(1) })
    act(() => { screen.getByRole('button', { name: 'Re-extract' }).click() })
    await waitFor(() => { expect(callsTo('/api/extract')).toHaveLength(1) }, { timeout: 4000 })
    await waitFor(() => { expect(after).toBe(1) }, { timeout: 4000 })
    await act(async () => { firstSave.release(); await settle(300) })
    const said = useStore.getState().log.filter(l => l.text.includes('s0003')).map(l => l.text)
    await act(async () => { reopen.release(); await settle(100) })

    expect(said.filter(t => t.includes('the page moved'))).toEqual([])
  }, 20000)

  // **Known broken, HANDOFF-096.** The page does not order its reads of a
  // document, so an older one can land over a newer one, and a save's re-read
  // is the only way the page learns what the save stored. Written by
  // HANDOFF-088's reviews against defects that fail on `83a865b` too, and
  // repaired there four ways that each opened another ordering; kept here as
  // `it.fails`, so the suite turns red the day the design lands and the marker
  // is removed in the same commit — the convention `KNOWN_BROKEN` follows in
  // `tests/test_pipeline.py`.
  it.fails('lets an open of a document yield to a later re-read of it that landed first (older)', async () => {
    // A hold's re-read asked after the open on the way back, and answered
    // first: the open's older read may not replace it — and the open still
    // finishes, or the page reads "reading…" for good.
    const holdGate = held()
    const backRead = held()
    const away = held()
    let holdDone = false
    const heldDoc: DocResponse = {
      ...doc,
      segments: doc.segments.map(s => (s.id === 's0001' ? { ...s, review: 'held' as const, token: 't1h' } : s)),
    }
    serve({
      hold: () => ({ body: { applied: 1, unknown: [] }, after: holdGate.until.then(() => { holdDone = true }) }),
      read: (src, n) => (src === doc.source && n === 2 ? { body: doc, after: backRead.until }
        : src === second.source && n === 1 ? { body: second, after: away.until }
          : src === doc.source && holdDone ? { body: heldDoc }
            : null),
    })
    await opened(address(doc.source, 's0001'), '/3 translated')
    act(() => { rowButton('Hold').click() })
    await waitFor(() => { expect(callsTo('/api/hold')).toHaveLength(1) })
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(readsOf(second.source)).toBe(1) })
    window.location.hash = address(doc.source, 's0001')
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await act(async () => { holdGate.release(); await settle(200) })
    await act(async () => { backRead.release(); away.release(); await settle(200) })
    expect(useStore.getState().docLoading).toBe(false)
    expect(screen.queryByText(/^reading book\//)).toBeNull()
    expect(useStore.getState().doc?.segments.find(s => s.id === 's0001')?.review).toBe('held')
  }, 20000)

  it('reports the wording it would strand before it reports a run in the way, when both are true', async () => {
    // A refusal over a run can be retried; stranded words cannot be recovered,
    // so that is the reason given first.
    serve()
    await opened(address(doc.source, 's0003'), '/3 translated')
    await userEvent.setup().click(screen.getByRole('button', { name: 'Re-extract' }))
    await waitFor(() => { expect(dialogOpen()).toBe(true) }, { timeout: 4000 })
    act(() => {
      drafts.set('s0002', '另一段。', '她沒有睡。')
      useStore.setState({ running: true })
    })
    await userEvent.setup().click(within(document.querySelector<HTMLDialogElement>('dialog[open]')!).getByRole('button', { name: 'Re-extract' }))
    await settle(200)

    expect(extracts()).toEqual([])
    expect(logged('warn', 'wording changed while this was asking')).toBe(true)
    expect(logged('warn', 'a run started')).toBe(false)
  }, 20000)

  // ── the seventh review: what was written, and where a confirmed act goes ─
  //
  // Ported from that review's probes. The fixture's save answers as the server
  // does, with what it stored; the page takes that at once.

  it('a "Draft again" confirmed after the page moved to another document is not sent to that document (older)', async () => {
    serve()
    await opened(address(doc.source, 's0001'), '/3 translated')
    act(() => { rowButton('Draft again').click() })
    await waitFor(() => { expect(dialogOpen()).toBe(true) })
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(useStore.getState().doc?.source).toBe(second.source) }, { timeout: 4000 })
    await waitFor(() => { expect(useStore.getState().docLoading).toBe(false) })
    expect(dialogOpen()).toBe(true)
    await replaceIt()
    await settle(300)
    const sentTo = callsTo('/api/translate').map(c => c.body)
    expect(sentTo).toEqual([])
  }, 20000)

  it('does not say the document on screen was being read again when the page was on its way to another one', async () => {
    const away = held()
    serve({ read: (src, n) => (src === second.source && n === 1 ? { body: second, after: away.until } : null) })
    await opened(address(doc.source, 's0001'), '/3 translated')
    act(() => { rowButton('Draft again').click() })
    await waitFor(() => { expect(dialogOpen()).toBe(true) })
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(readsOf(second.source)).toBe(1) })
    await replaceIt()
    await settle(300)
    const said = useStore.getState().log.filter(l => l.text.includes('s0001')).map(l => l.text)
    await act(async () => { away.release(); await settle(100) })
    expect(said.filter(t => t.includes('being read again') && !t.includes('replaced'))).toEqual([])
  }, 20000)

  it('does not say the document on screen was being read again when the page declined to leave it', async () => {
    serve({ save: () => refused })
    await opened(address(doc.source, 's0001'), '/3 translated')
    await userEvent.setup().type(field(), '！')
    act(() => { rowButton('Draft again').click() })
    await waitFor(() => { expect(dialogOpen()).toBe(true) })
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(useStore.getState().docError).toContain('could not be written') }, { timeout: 4000 })
    await replaceIt()
    await settle(300)
    const said = useStore.getState().log.filter(l => l.text.includes('s0001') && l.text.includes('not sent')).map(l => l.text)
    expect(said.filter(t => t.includes('being read again') && !t.includes('replaced'))).toEqual([])
  }, 20000)

  // **Known broken, HANDOFF-096.** The page does not order its reads of a
  // document, so an older one can land over a newer one, and a save's re-read
  // is the only way the page learns what the save stored. Written by
  // HANDOFF-088's reviews against defects that fail on `83a865b` too, and
  // repaired there four ways that each opened another ordering; kept here as
  // `it.fails`, so the suite turns red the day the design lands and the marker
  // is removed in the same commit — the convention `KNOWN_BROKEN` follows in
  // `tests/test_pipeline.py`.
  it.fails('asks before "Draft again" over wording its own save just wrote, when that save\'s re-read fails (older)', async () => {
    let saved = false
    serve({
      base: untranslated,
      save: () => { saved = true; return null },
      read: (src, n) => (src === doc.source && n === 2 ? unreachable()
        : src === doc.source && saved ? { body: withWords }
          : null),
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    await userEvent.setup().type(field(), '燈還亮著。')
    act(() => { rowButton('Draft again').click() })
    await settle(400)
    expect(saved).toBe(true)
    expect({ sent: callsTo('/api/translate').map(c => c.body), asked: dialogOpen() })
      .toEqual({ sent: [], asked: true })
  }, 20000)

  // **Known broken, HANDOFF-096.** The page does not order its reads of a
  // document, so an older one can land over a newer one, and a save's re-read
  // is the only way the page learns what the save stored. Written by
  // HANDOFF-088's reviews against defects that fail on `83a865b` too, and
  // repaired there four ways that each opened another ordering; kept here as
  // `it.fails`, so the suite turns red the day the design lands and the marker
  // is removed in the same commit — the convention `KNOWN_BROKEN` follows in
  // `tests/test_pipeline.py`.
  it.fails('asks before "Draft again" over wording a blur wrote whose re-read is still on its way (older)', async () => {
    const saveRead = held()
    let saved = false
    serve({
      base: untranslated,
      save: () => { saved = true; return null },
      read: (src, n) => (src === doc.source && n === 2 ? { body: withWords, after: saveRead.until }
        : src === doc.source && saved ? { body: withWords }
          : null),
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    const user = userEvent.setup()
    await user.type(field(), '燈還亮著。')
    await user.click(screen.getByText('The lamp was still burning.'))
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    expect(saved).toBe(true)
    await user.click(rowButton('Draft again'))
    await settle(300)
    const sent = callsTo('/api/translate').map(c => c.body)
    const asked = dialogOpen()
    await act(async () => { saveRead.release(); await settle(100) })
    expect({ sent, asked }).toEqual({ sent: [], asked: true })
  }, 20000)

  // **Known broken, HANDOFF-096.** The page does not order its reads of a
  // document, so an older one can land over a newer one, and a save's re-read
  // is the only way the page learns what the save stored. Written by
  // HANDOFF-088's reviews against defects that fail on `83a865b` too, and
  // repaired there four ways that each opened another ordering; kept here as
  // `it.fails`, so the suite turns red the day the design lands and the marker
  // is removed in the same commit — the convention `KNOWN_BROKEN` follows in
  // `tests/test_pipeline.py`.
  it.fails('keeps the reviewer\'s next edit when the re-read after their first save failed (older)', async () => {
    let saved = false
    serve({
      base: untranslated,
      save: (_n, body) => {
        const base = (body as { base?: Record<string, string> }).base ?? {}
        if (saved && base['s0003'] !== 'w3') {
          return { body: { applied: 0, unknown: [], stored: {}, conflicts: { s0003: { text: '燈還亮著。', token: 'w3' } } } }
        }
        saved = true
        return { body: { applied: 1, unknown: [], stored: { s0003: { text: '燈還亮著。', token: 'w3' } }, conflicts: {} } }
      },
      read: (src, n) => (src === doc.source && n === 2 ? unreachable()
        : src === doc.source && saved ? { body: withWords }
          : null),
    })
    await opened(address(doc.source, 's0003'), '/3 translated')
    const user = userEvent.setup()
    await user.type(field(), '燈還亮著。')
    await user.click(screen.getByText('The lamp was still burning.'))
    await waitFor(() => { expect(callsTo('/api/save')).toHaveLength(1) })
    await settle(200)
    await user.type(field(), '還')
    await user.click(screen.getByText('The lamp was still burning.'))
    await waitFor(() => { expect(callsTo('/api/save')).toHaveLength(2) })
    await settle(300)
    expect({ lost: logged('bad', 'changed underneath this edit'), field: field().value })
      .toEqual({ lost: false, field: '燈還亮著。還' })
  }, 20000)

  // **Known broken, HANDOFF-096.** The page does not order its reads of a
  // document, so an older one can land over a newer one, and a save's re-read
  // is the only way the page learns what the save stored. Written by
  // HANDOFF-088's reviews against defects that fail on `83a865b` too, and
  // repaired there four ways that each opened another ordering; kept here as
  // `it.fails`, so the suite turns red the day the design lands and the marker
  // is removed in the same commit — the convention `KNOWN_BROKEN` follows in
  // `tests/test_pipeline.py`.
  it.fails('an open that a later re-read of its document overtook does not draw an error over that re-read when its own read fails (older)', async () => {
    const holdGate = held()
    const backRead = held()
    const away = held()
    let holdDone = false
    const heldDoc: DocResponse = {
      ...doc,
      segments: doc.segments.map(s => (s.id === 's0001' ? { ...s, review: 'held' as const, token: 't1h' } : s)),
    }
    serve({
      hold: () => ({ body: { applied: 1, unknown: [] }, after: holdGate.until.then(() => { holdDone = true }) }),
      read: (src, n) => (src === doc.source && n === 2 ? { body: {}, after: backRead.until.then(() => { throw new TypeError('Failed to fetch') }) }
        : src === second.source && n === 1 ? { body: second, after: away.until }
          : src === doc.source && holdDone ? { body: heldDoc }
            : null),
    })
    await opened(address(doc.source, 's0001'), '/3 translated')
    act(() => { rowButton('Hold').click() })
    await waitFor(() => { expect(callsTo('/api/hold')).toHaveLength(1) })
    window.location.hash = address(second.source, 's0001')
    await waitFor(() => { expect(readsOf(second.source)).toBe(1) })
    window.location.hash = address(doc.source, 's0001')
    await waitFor(() => { expect(readsOf(doc.source)).toBe(2) })
    await act(async () => { holdGate.release(); await settle(200) })
    await waitFor(() => { expect(readsOf(doc.source)).toBe(3) })
    expect(useStore.getState().doc?.segments.find(s => s.id === 's0001')?.review).toBe('held')
    await act(async () => { backRead.release(); await settle(200) })
    await act(async () => { away.release(); await settle(100) })
    expect({ docError: useStore.getState().docError, loading: useStore.getState().docLoading })
      .toEqual({ docError: '', loading: false })
  }, 20000)
})

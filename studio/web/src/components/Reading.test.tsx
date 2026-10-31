/**
 * The reading view at a novel's scale, and the cut that makes it virtualizable.
 *
 * HANDOFF-090: a five-thousand-segment document drawn here in one pass was 9999
 * `.block` spans, and *Read → click → Back* froze the maintainer's browser for
 * about ten seconds. The view is virtualized by line since then, so two things
 * need pinning: that it mounts a bounded number of blocks however long the
 * chapter is, and that cutting the chapter into lines neither adds, drops nor
 * reorders a byte of what the server rendered.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Line, Reading, lines } from './Reading'
import type { Block, DocResponse, Segment } from '../contract'
import * as routes from '../router'
import { useStore } from '../store'
import { answering, callsTo } from '../test/wire'

const seg = (id: string, text: string, kind: Block['kind'] = 'para'): Block =>
  ({ id, kind, from: 'target', text })
const skel = (text: string): Block => ({ id: null, kind: null, from: null, text })

const joined = (rows: ReturnType<typeof lines>): string =>
  rows.map(line => line.map(p => p.text).join('')).join('')

describe('lines', () => {
  // The shapes the Markdown and plain-text parsers actually leave between
  // segments: a blank line, several, a list marker on the next line, a marker
  // with no break, a segment carrying its own wrapped lines, an empty run, and
  // skeleton at both ends of the document.
  const book: Block[] = [
    skel('\n'),
    seg('s0001', 'Chapter One', 'heading'),
    skel('\n\n'),
    seg('s0002', 'The keeper counted ships,\n  writing their names down.'),
    skel('\n\n\n\n'),
    seg('s0003', 'Nobody else read the ledger.'),
    skel('\n\n- '),
    seg('s0004', 'a lamp', 'list'),
    skel('\n- '),
    seg('s0005', 'a rope', 'list'),
    skel(''),
    skel(' — '),
    seg('s0006', 'and a bell', 'list'),
    skel('\n'),
  ]

  it('concatenates back to the document byte for byte', () => {
    expect(joined(lines(book))).toBe(book.map(b => b.text).join(''))
  })

  it('never divides a segment, and draws each one exactly once', () => {
    const drawn = lines(book).flat().filter(p => p.block.id != null)
    expect(drawn.map(p => p.block.id)).toEqual(['s0001', 's0002', 's0003', 's0004', 's0005', 's0006'])
    for (const p of drawn) expect(p.text).toBe(p.block.text)
  })

  it('ends a line at a skeleton run\'s first break and opens the next with the rest', () => {
    const rows = lines([seg('a', 'one'), skel('\n\n'), seg('b', 'two')])
    expect(rows.map(line => line.map(p => p.text))).toEqual([['one', '\n'], ['\n', 'two']])
  })

  it('keeps a list marker on the line of the item it marks', () => {
    const rows = lines([seg('a', 'intro'), skel('\n- '), seg('b', 'item')])
    expect(rows.map(line => line.map(p => p.text))).toEqual([['intro', '\n'], ['- ', 'item']])
  })

  it('puts a block on a line at most once, which is what keys the pieces', () => {
    for (const line of lines(book)) {
      const seen = line.map(p => p.index)
      expect(new Set(seen).size).toBe(seen.length)
    }
  })
})

describe('the reading view at a novel\'s scale', () => {
  const initial = useStore.getState()
  const N = 5000

  const segments: Segment[] = Array.from({ length: N }, (_, i) => ({
    id: `s${String(i + 1).padStart(4, '0')}`, kind: 'para', status: 'translated', origin: 'human',
    source: `Paragraph ${i + 1}.`, target: `第 ${i + 1} 段。`, review: null, waived: false,
    token: `t${i}`, issues: [],
  }))
  const doc: DocResponse = {
    source: 'book/long.md', lang: 'zh-TW', tone: 'literary',
    report: { segments: N, translated: N, errors: 0, warnings: 0, by_rule: {} },
    segments,
  }
  const blocks: Block[] = segments.flatMap((s, i) =>
    i === 0 ? [seg(s.id, s.target)] : [skel('\n\n'), seg(s.id, s.target)])

  beforeEach(() => {
    useStore.setState({ ...initial, doc }, true)
    window.location.hash = `#/read/zh-TW/${encodeURIComponent(doc.source)}?seg=s2500`
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    answering(call => {
      if (call.path.startsWith('/api/preview')) {
        return { body: { text: blocks.map(b => b.text).join(''), blocks, missing: 0, default_out: 'out.md' } }
      }
      if (call.path.startsWith('/api/sentences')) return { body: { sentences: [['第 2500 段。']] } }
      return null
    })
  })

  afterEach(() => {
    useStore.setState(initial, true)
    window.location.hash = ''
  })

  /**
   * Bounded, not "small": jsdom lays nothing out, so `virtua` mounts the line
   * `keepMounted` names and nothing for its range — in a browser it is the lines
   * in the viewport plus its buffer. Either way it is not the chapter, and the
   * build before HANDOFF-090 mounted every one of the 5000 segments here.
   */
  it('mounts a bounded number of blocks for a chapter of five thousand', async () => {
    render(<Reading />)
    await waitFor(() => {
      expect(document.getElementById('b-s2500')?.getAttribute('aria-current')).toBe('true')
    }, { timeout: 4000 })
    const mounted = document.querySelectorAll('.block.seg').length
    expect(mounted).toBeGreaterThan(0)
    expect(mounted).toBeLessThan(100)
    expect(document.querySelectorAll('.block').length).toBeLessThan(200)
    // The paragraph the address names is drawn in its sentences: the line the
    // reviewer is on is the one kept mounted, so the marks have somewhere to go.
    await waitFor(() => { expect(document.querySelector('#b-s2500 .sentence')).toBeTruthy() })
    expect(callsTo('/api/preview')).toHaveLength(1)
  }, 15000)

  /**
   * Tab moves focus and not the address, so the line holding focus is kept
   * mounted on its own. Without it, measured in headless Chrome by HANDOFF-090's
   * review, a PageDown from a paragraph reached by Tab unmounted the focused
   * element and keyboard scrolling stopped. jsdom scrolls nothing, so the door
   * here is the address moving while focus stays: the line the address leaves
   * would be dropped from `keepMounted`, and the focused element with it.
   */
  it('keeps the line keyboard focus is in mounted when the address moves away from it', async () => {
    render(<Reading />)
    await waitFor(() => { expect(document.getElementById('b-s2500')).toBeTruthy() }, { timeout: 4000 })
    const focused = document.getElementById('b-s2500')!
    act(() => { focused.focus() })
    act(() => {
      window.location.hash = `#/read/zh-TW/${encodeURIComponent(doc.source)}?seg=s0007`
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    })
    await waitFor(() => {
      expect(document.getElementById('b-s0007')?.getAttribute('aria-current')).toBe('true')
    })
    expect(document.getElementById('b-s2500')).toBe(focused)
    expect(document.activeElement).toBe(focused)
  }, 15000)
})

/**
 * A line's handlers, clicked beside the list rather than inside it — jsdom
 * mounts no line for `VList`'s range, and mocking its measurement would leave a
 * test that passes over a view that shows nothing.
 */
describe('a line of the reading view', () => {
  afterEach(() => { window.location.hash = '' })

  it('moves the address with a click and opens the ledger on Enter', async () => {
    window.location.hash = '#/read/zh-TW/book%2Fch1.md?seg=s0001'
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    const said: string[] = []
    const [line] = lines([seg('s0001', '第一句。'), skel(' '), seg('s0002', '第二句。')])
    render(
      <Line line={line!} first src="book/ch1.md" lang="zh-TW" current="s0001" pieces={null}
        failing={id => id === 's0002'} say={t => { said.push(t) }} />)
    expect(document.getElementById('b-s0002')?.className).toContain('failing')

    const user = userEvent.setup()
    await user.click(document.getElementById('b-s0002')!)
    await waitFor(() => { expect(window.location.hash).toBe('#/read/zh-TW/book%2Fch1.md?seg=s0002') })

    act(() => { document.getElementById('b-s0002')!.focus() })
    await user.keyboard('{Enter}')
    await waitFor(() => { expect(window.location.hash).toBe('#/doc/zh-TW/book%2Fch1.md?seg=s0002') })
    expect(said).toEqual(['opening s0002 in the ledger'])
    expect(screen.getAllByRole('button')).toHaveLength(2)
  })
})

/**
 * The tests below were written by an independent mutation pass over the first
 * commit of HANDOFF-090: 31 mutants, of which 14 survived the suite as real
 * gaps. Each block names the mutants it kills, so a reader knows what would
 * break if it were deleted.
 */
const docOf = (source: string, ids: string[]): DocResponse => {
  const segments: Segment[] = ids.map((id, i) => ({
    id, kind: 'para', status: 'translated', origin: 'human',
    source: `P${i}`, target: `T${i}`, review: null, waived: false, token: `t${i}`, issues: [],
  }))
  return {
    source, lang: 'zh-TW', tone: 'literary',
    report: { segments: ids.length, translated: ids.length, errors: 0, warnings: 0, by_rule: {} },
    segments,
  }
}

/**
 * A `ResizeObserver` that answers: the scroller is 800 px tall and every line
 * 30. jsdom lays nothing out, so without this `virtua` mounts nothing for its
 * range, and a bound on what it mounts measures only `keepMounted` — the bound
 * above is met by any range at all, which is how a mutant that widened the
 * range to the whole chapter survived it. One delivery per task with every
 * element observed since, as a browser batches them: one callback per element
 * makes a five-thousand-line list quadratic, and the first version hung.
 */
function measuring(): () => void {
  const win = document.defaultView as unknown as Record<string, unknown>
  const g = globalThis as unknown as Record<string, unknown>
  const before = { win: win.ResizeObserver, g: g.ResizeObserver }
  const desc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetParent')
  class Measuring {
    private queue: Element[] = []
    constructor(private cb: ResizeObserverCallback) {}
    observe(el: Element): void {
      if (this.queue.push(el) > 1) return
      setTimeout(() => {
        const entries = this.queue.splice(0).map(target => ({
          target, contentRect: { height: target.classList.contains('reading') ? 800 : 30, width: 600 },
        }))
        this.cb(entries as unknown as ResizeObserverEntry[], this as unknown as ResizeObserver)
      }, 0)
    }
    unobserve(): void { /* nothing */ }
    disconnect(): void { /* nothing */ }
  }
  win.ResizeObserver = Measuring
  g.ResizeObserver = Measuring
  Object.defineProperty(HTMLElement.prototype, 'offsetParent', { configurable: true, get() { return document.body } })
  return () => {
    win.ResizeObserver = before.win
    g.ResizeObserver = before.g
    if (desc) Object.defineProperty(HTMLElement.prototype, 'offsetParent', desc)
  }
}

describe('the reading view, measured', () => {
  const initial = useStore.getState()
  let undo: (() => void) | null = null
  afterEach(() => {
    undo?.()
    undo = null
    useStore.setState(initial, true)
    window.location.hash = ''
    vi.restoreAllMocks()
  })

  const N = 5000
  const ids = Array.from({ length: N }, (_, i) => `s${String(i + 1).padStart(4, '0')}`)
  const big = docOf('book/long.md', ids)
  const bigBlocks: Block[] = ids.flatMap((id, i) =>
    i === 0 ? [seg(id, `第 ${i + 1} 段。`)] : [skel('\n\n'), seg(id, `第 ${i + 1} 段。`)])

  const open = (doc: DocResponse, blocks: Block[], hash: string, sentences?: (texts: string[]) => Promise<void> | undefined): void => {
    useStore.setState({ ...initial, doc }, true)
    window.location.hash = hash
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    answering(call => {
      if (call.path.startsWith('/api/preview')) {
        return { body: { text: blocks.map(b => b.text).join(''), blocks, missing: 0, default_out: 'out.md' } }
      }
      if (call.path.startsWith('/api/sentences')) {
        const texts = (call.body as { texts: string[] }).texts
        return { body: { sentences: [texts] }, after: sentences?.(texts) }
      }
      return null
    })
  }

  // Kills a range widened to the whole chapter (`bufferSize` of a billion).
  it('mounts a screenful of a five-thousand-line chapter once the list has a viewport', async () => {
    undo = measuring()
    open(big, bigBlocks, `#/read/zh-TW/${encodeURIComponent(big.source)}?seg=s2500`)
    render(<Reading />)
    await waitFor(() => {
      expect(document.getElementById('b-s2500')?.getAttribute('aria-current')).toBe('true')
      // The range really is mounted, so the bound below is not met vacuously.
      expect(document.querySelectorAll('.block.seg').length).toBeGreaterThan(10)
    }, { timeout: 4000 })
    await act(async () => { await new Promise(r => { setTimeout(r, 100) }) })
    expect(document.querySelectorAll('.block.seg').length).toBeLessThan(100)
  }, 15000)

  // Kills: the class dropped from the scroller; a divided skeleton run drawn
  // whole on both of its lines; skeleton swallowed; the margins on the wrong
  // lines. What is on screen is the server's text, byte for byte.
  it('draws the rendered document byte for byte, with the margins on its first and last line', async () => {
    undo = measuring()
    const book: Block[] = [
      skel('\n'), seg('s0001', 'Chapter One', 'heading'), skel('\n\n'),
      seg('s0002', 'The keeper counted ships,\n  writing their names down.'), skel('\n\n\n\n'),
      seg('s0003', 'Nobody else read the ledger.'), skel('\n\n- '), seg('s0004', 'a lamp', 'list'),
      skel('\n- '), seg('s0005', 'a rope', 'list'), skel(''), skel(' — '), seg('s0006', 'and a bell', 'list'),
      skel('\n'),
    ]
    const small = docOf('book/small.md', ['s0001', 's0002', 's0003', 's0004', 's0005', 's0006'])
    open(small, book, `#/read/zh-TW/${encodeURIComponent(small.source)}`)
    render(<Reading />)
    const want = lines(book).length
    await waitFor(() => { expect(document.querySelectorAll('.line')).toHaveLength(want) }, { timeout: 4000 })
    const scroller = document.getElementById('b-s0001')!.closest('.reading')
    expect(scroller).not.toBeNull()
    expect(scroller!.textContent).toBe(book.map(b => b.text).join(''))
    const drawn = [...document.querySelectorAll<HTMLElement>('.line')]
    expect(drawn[0]!.style.paddingTop).toBe('48px')
    expect(drawn[0]!.style.paddingBottom).toBe('')
    expect(drawn.at(-1)!.style.paddingBottom).toBe('40vh')
    expect(drawn.at(-1)!.style.paddingTop).toBe('')
    for (const line of drawn.slice(1, -1)) expect(line.getAttribute('style')).toBeNull()
  }, 15000)

  // Kills: `keep` not following the address; sentences asked for another
  // paragraph than the one the address names.
  it('keeps the line the address moved to mounted, and asks for that paragraph\'s sentences', async () => {
    open(big, bigBlocks, `#/read/zh-TW/${encodeURIComponent(big.source)}?seg=s2500`)
    render(<Reading />)
    await waitFor(() => {
      expect(document.getElementById('b-s2500')?.getAttribute('aria-current')).toBe('true')
    }, { timeout: 4000 })
    expect(callsTo('/api/sentences')[0]?.body).toEqual({ texts: ['第 2500 段。'] })
    act(() => { routes.focus(big.source, 'zh-TW', 's4000') })
    await waitFor(() => {
      expect(document.getElementById('b-s4000')?.getAttribute('aria-current')).toBe('true')
    })
  }, 15000)

  // Kills: the landing marked only when a paragraph was found. Arriving with no
  // paragraph named is the landing, so the first click must not scroll.
  it('does not scroll under the first click when it opened on no paragraph', async () => {
    open(big, bigBlocks, `#/read/zh-TW/${encodeURIComponent(big.source)}`)
    render(<Reading />)
    await waitFor(() => { expect(document.querySelector('.reading-bar')?.textContent).toContain('writes to out.md') })
    await act(async () => { await new Promise(r => { setTimeout(r, 50) }) })
    const scrolled = vi.spyOn(Element.prototype, 'scrollTo')
    const centred = vi.spyOn(Element.prototype, 'scrollIntoView')
    act(() => { routes.focus(big.source, 'zh-TW', 's2500') })
    await waitFor(() => {
      expect(document.getElementById('b-s2500')?.getAttribute('aria-current')).toBe('true')
    })
    await act(async () => { await new Promise(r => { setTimeout(r, 50) }) })
    expect(scrolled).not.toHaveBeenCalled()
    expect(centred).not.toHaveBeenCalled()
  }, 15000)

  /**
   * A paragraph the address has just moved to is drawn in its own text until
   * its own sentences arrive — never in the pieces of the paragraph before it.
   * Found by the same pass, and older than HANDOFF-090: the pieces were held
   * with no record of whose they were, so moving from s0020 to s0040 drew
   * s0040 as "第 20 段。" until the second request answered. Reproduced on the
   * parent commit too.
   */
  it('draws a newly focused paragraph in its own text while its sentences are asked for', async () => {
    let release: () => void = () => undefined
    const held = new Promise<void>(r => { release = r })
    let n = 0
    const fifty = ids.slice(0, 50)
    open(docOf('book/x.md', fifty), bigBlocks.slice(0, 99), `#/read/zh-TW/book%2Fx.md?seg=s0020`,
      () => { n += 1; return n === 1 ? undefined : held })
    render(<Reading />)
    await waitFor(() => { expect(document.querySelector('#b-s0020 .sentence')?.textContent).toBe('第 20 段。') }, { timeout: 4000 })
    act(() => { routes.focus('book/x.md', 'zh-TW', 's0040') })
    await waitFor(() => { expect(document.getElementById('b-s0040')?.getAttribute('aria-current')).toBe('true') })
    const shown = document.getElementById('b-s0040')!.textContent
    release()
    expect(shown).toBe('第 40 段。')
    await waitFor(() => { expect(document.querySelector('#b-s0040 .sentence')?.textContent).toBe('第 40 段。') })
  }, 15000)
})

describe('a line of the reading view, by hand', () => {
  afterEach(() => { window.location.hash = '' })

  // Kills: pieces handed to every paragraph, joined, or reversed.
  it('draws the focused paragraph in its sentences, in order, and no other paragraph in them', () => {
    const [line] = lines([seg('s0001', '第一句。第二句。'), skel(' '), seg('s0002', '另一段。')])
    render(
      <Line line={line!} first src="book/ch1.md" lang="zh-TW" current="s0001" pieces={['第一句。', '第二句。']}
        failing={() => false} say={() => undefined} />)
    expect([...document.querySelectorAll('#b-s0001 .sentence')].map(e => e.textContent)).toEqual(['第一句。', '第二句。'])
    expect(document.querySelectorAll('#b-s0002 .sentence')).toHaveLength(0)
    expect(document.getElementById('b-s0002')!.textContent).toBe('另一段。')
  })

  // Kills: Space no longer picking; a double-click picking instead of opening.
  it('picks on Space and opens the ledger on a double-click', async () => {
    window.location.hash = '#/read/zh-TW/book%2Fch1.md?seg=s0002'
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    const [line] = lines([seg('s0001', '第一句。'), skel(' '), seg('s0002', '第二句。')])
    render(
      <Line line={line!} first src="book/ch1.md" lang="zh-TW" current="s0002" pieces={null}
        failing={() => false} say={() => undefined} />)
    const user = userEvent.setup()
    act(() => { document.getElementById('b-s0001')!.focus() })
    await user.keyboard(' ')
    await waitFor(() => { expect(window.location.hash).toBe('#/read/zh-TW/book%2Fch1.md?seg=s0001') })
    await user.dblClick(document.getElementById('b-s0002')!)
    await waitFor(() => { expect(window.location.hash).toBe('#/doc/zh-TW/book%2Fch1.md?seg=s0002') })
  })

  // Kills: the top margin on the last line, or the run-out on the first.
  it('puts the top margin on the first line only and the run-out on the last only', () => {
    const [line] = lines([seg('s0001', 'x')])
    const { container, rerender } = render(
      <Line line={line!} first={false} last src="s" lang="zh-TW" current={null} pieces={null}
        failing={() => false} say={() => undefined} />)
    const div = (): HTMLElement => container.querySelector<HTMLElement>('.line')!
    expect(div().style.paddingTop).toBe('')
    expect(div().style.paddingBottom).toBe('40vh')
    rerender(
      <Line line={line!} first last={false} src="s" lang="zh-TW" current={null} pieces={null}
        failing={() => false} say={() => undefined} />)
    expect(div().style.paddingTop).toBe('48px')
    expect(div().style.paddingBottom).toBe('')
  })
})

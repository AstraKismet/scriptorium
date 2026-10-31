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
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { Line, Reading, lines } from './Reading'
import type { Block, DocResponse, Segment } from '../contract'
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
   * in the viewport plus its overscan. Either way it is not the chapter, and the
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

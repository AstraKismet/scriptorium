import { afterEach, describe, expect, it } from 'vitest'

import * as routes from './router'

describe('the address', () => {
  it('round-trips a document path that contains separators', () => {
    const link = routes.doc('books/vol 1/ch1.md', 'zh-TW')
    expect(routes.parse(link)).toEqual({
      name: 'doc', src: 'books/vol 1/ch1.md', lang: 'zh-TW', seg: null,
    })
  })

  it('carries the paragraph, so a reload lands where the reviewer was', () => {
    expect(routes.parse(routes.read('a.md', 'zh-TW', 's0042'))).toEqual({
      name: 'read', src: 'a.md', lang: 'zh-TW', seg: 's0042',
    })
  })

  it('reads a hand-typed link whose separators were left bare', () => {
    expect(routes.parse('#/doc/zh-TW/books/ch1.md')).toEqual({
      name: 'doc', src: 'books/ch1.md', lang: 'zh-TW', seg: null,
    })
  })

  it('falls home rather than throwing on a malformed escape', () => {
    // A bad link must not take the whole application down.
    expect(routes.parse('#/doc/zh-TW/%E0%A4%A')).toEqual({ name: 'home' })
    expect(routes.parse('#/nonsense')).toEqual({ name: 'home' })
    expect(routes.parse('')).toEqual({ name: 'home' })
  })
})

describe('moving the segment', () => {
  afterEach(() => { window.history.replaceState(null, '', '#') })

  // The expected addresses are spelled out rather than built with `routes.read`
  // and `routes.doc`, which share the function `focus` writes with — a defect
  // there would move both sides of the comparison together.
  it('writes the segment into the address of the document it names, keeping its kind', () => {
    window.history.replaceState(null, '', '#/read/zh-TW/a.md?seg=s0001')
    routes.focus('a.md', 'zh-TW', 's0004')
    expect(window.location.hash).toBe('#/read/zh-TW/a.md?seg=s0004')
  })

  /**
   * `go` moves `location.hash` at once and `hashchange` arrives a task later, so
   * a row of the document being left can still take focus in between. Its id
   * would be a paragraph of the next document that nobody chose — ids restart
   * at `s0001` in every one.
   */
  it('writes nothing into the address of another document', () => {
    window.history.replaceState(null, '', '#/doc/zh-TW/b.md')
    routes.focus('a.md', 'zh-TW', 's0003')
    expect(window.location.hash).toBe('#/doc/zh-TW/b.md')
    routes.focus('b.md', 'en', 's0003')
    expect(window.location.hash).toBe('#/doc/zh-TW/b.md')
  })

  it('writes nothing where the address names no document', () => {
    window.history.replaceState(null, '', '#/backends')
    routes.focus('a.md', 'zh-TW', 's0003')
    expect(window.location.hash).toBe('#/backends')
  })

  it('remembers a paragraph per document and per language', async () => {
    const announced = new Promise<void>(r => { window.addEventListener('hashchange', () => { r() }, { once: true }) })
    window.location.hash = '#/doc/zh-TW/c.md?seg=s0007'
    await announced
    expect(routes.placeIn('c.md', 'zh-TW')).toBe('s0007')
    expect(routes.placeIn('c.md', 'ja')).toBeNull()
    expect(routes.placeIn('d.md', 'zh-TW')).toBeNull()
  })
})

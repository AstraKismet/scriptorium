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

  it('writes the segment into the address of the document it names, keeping its kind', () => {
    window.history.replaceState(null, '', routes.read('a.md', 'zh-TW', 's0001'))
    routes.focus('a.md', 'zh-TW', 's0004')
    expect(window.location.hash).toBe(routes.read('a.md', 'zh-TW', 's0004'))
  })

  /**
   * `go` moves `location.hash` at once and `hashchange` arrives a task later, so
   * a row of the document being left can still take focus in between. Its id
   * would be a paragraph of the next document that nobody chose — ids restart
   * at `s0001` in every one.
   */
  it('writes nothing into the address of another document', () => {
    window.history.replaceState(null, '', routes.doc('b.md', 'zh-TW'))
    routes.focus('a.md', 'zh-TW', 's0003')
    expect(window.location.hash).toBe(routes.doc('b.md', 'zh-TW'))
    routes.focus('b.md', 'en', 's0003')
    expect(window.location.hash).toBe(routes.doc('b.md', 'zh-TW'))
  })

  it('writes nothing where the address names no document', () => {
    window.history.replaceState(null, '', routes.backends())
    routes.focus('a.md', 'zh-TW', 's0003')
    expect(window.location.hash).toBe(routes.backends())
  })
})

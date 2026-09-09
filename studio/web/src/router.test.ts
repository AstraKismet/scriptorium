import { describe, expect, it } from 'vitest'

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

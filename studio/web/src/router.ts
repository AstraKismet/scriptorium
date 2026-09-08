/**
 * Hash-fragment routing, and nothing else.
 *
 * The server answers an unknown static path with `404` and the plain-text body
 * `not found` — deliberately **not** `index.html` with a `200`, because
 * answering a typo with a success made every mistake render as a blank
 * application and made a traversal attempt look as though it had been served.
 * That branch is frozen in the contract's *Static assets* section, so path-mode
 * routing is not available without a version decision. A hash fragment never
 * reaches the server at all, which is the whole reason it is the answer here:
 * deep links, reload safety and the back button at zero contract cost.
 *
 * A document is addressed `lang` then `src`, in that order and both
 * percent-encoded, because `src` may contain `/` and `lang` may not: a language
 * tag is letters, digits, `-` and `_`, matched against the whole value, so it
 * cannot be mistaken for a path segment.
 *
 * **The address carries the segment too**, after a `?`. On a five-thousand-
 * segment novel a reload that lands at the top of the book has lost the
 * reviewer's place, and the ledger and the reading view are two ways of looking
 * at the same paragraph — the round trip between them is only worth making if it
 * comes back to where it started. It is written with `replaceState` rather than
 * by assigning to `location.hash`, because a history entry per row would make
 * the back button walk a chapter one paragraph at a time.
 */
import { useSyncExternalStore } from 'react'

export type Route =
  | { name: 'home' }
  | { name: 'doc'; src: string; lang: string; seg: string | null }
  | { name: 'read'; src: string; lang: string; seg: string | null }
  | { name: 'backends' }
  | { name: 'routing' }

const at = (kind: 'doc' | 'read', src: string, lang: string, seg?: string | null): string =>
  `#/${kind}/${encodeURIComponent(lang)}/${encodeURIComponent(src)}` +
  (seg ? `?seg=${encodeURIComponent(seg)}` : '')

export const home = (): string => '#/'
export const doc = (src: string, lang: string, seg?: string | null): string => at('doc', src, lang, seg)
export const read = (src: string, lang: string, seg?: string | null): string => at('read', src, lang, seg)
export const backends = (): string => '#/backends'
export const routing = (): string => '#/routing'

export function parse(hash: string): Route {
  const [path = '', query = ''] = hash.replace(/^#\/?/, '').split('?', 2)
  const parts = path.split('/')
  const head = parts[0] ?? ''
  if (head === 'backends') return { name: 'backends' }
  if (head === 'routing') return { name: 'routing' }
  if (head === 'doc' || head === 'read') {
    const lang = parts[1] ?? ''
    // Everything after the language is the document path. It was encoded whole,
    // so it is one element — but a hand-typed link may have left the slashes
    // bare, and rejoining is what makes that work rather than truncating at the
    // first separator.
    const src = parts.slice(2).join('/')
    if (!lang || !src) return { name: 'home' }
    try {
      return {
        name: head,
        src: decodeURIComponent(src),
        lang: decodeURIComponent(lang),
        seg: new URLSearchParams(query).get('seg'),
      }
    } catch {
      // A malformed percent-escape. Nothing to show, and throwing here would
      // take the whole application down over a bad link.
      return { name: 'home' }
    }
  }
  return { name: 'home' }
}

const listeners = new Set<() => void>()
let current = window.location.hash

const announce = (): void => {
  current = window.location.hash
  for (const l of listeners) l()
}

window.addEventListener('hashchange', announce)

const subscribe = (l: () => void): (() => void) => {
  listeners.add(l)
  return () => { listeners.delete(l) }
}

// The snapshot is the raw hash string, not the parsed object: `useSyncExternalStore`
// compares snapshots with `Object.is`, and a fresh object every call is an
// infinite render loop.
const snapshot = (): string => current

export function useRoute(): Route {
  return parse(useSyncExternalStore(subscribe, snapshot, snapshot))
}

/** Navigate. This is a history entry — the back button should undo it. */
export const go = (to: string): void => { window.location.hash = to }

/**
 * Move the address without adding a history entry.
 *
 * For view state a person did not navigate to: which paragraph they are on,
 * which filter is showing. `hashchange` does not fire for `replaceState`, so the
 * subscribers are told directly.
 */
export function replace(to: string): void {
  if (window.location.hash === to) return
  window.history.replaceState(null, '', to)
  announce()
}

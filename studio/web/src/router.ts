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
 *
 * **And the address is the only place the segment is kept.** Which paragraph a
 * reviewer is on is read from here by every component that needs it, and a row
 * moves it by writing here — there is no copy of it anywhere else. There was one
 * until HANDOFF-084: the store held a `focused` beside the address and two
 * effects kept each in step with the other, so the moment both named a segment
 * and the two differed, each effect wrote its own side over the other in the
 * same commit, the next commit found them swapped, and React stopped the loop
 * with error #185 and unmounted the page. A click on a second segment did it,
 * and so did Back or a link naming another paragraph. Switching documents did
 * not blank the page; it carried the old document's segment into the new one.
 * One copy cannot disagree with itself, which is the whole of the repair.
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

/**
 * The paragraph the address last named in each document, for the links that
 * address a document without knowing the paragraph — the rail's, and the
 * toolbar's Read.
 *
 * Without it, opening the chapter that is already open, or coming back to it
 * from the backend screens, would land a five-thousand-segment novel at its top.
 * It is **written only here, from the address**, and read only when a person
 * follows a link, so it is a record of where the address has been rather than a
 * second answer to where it is: nothing reconciles it with anything, and a
 * paragraph remembered under one document can only be offered for that document.
 */
const places = new Map<string, string | null>()
// Not `lang + '/' + src`: `parse` does not validate a language tag, so a
// hand-typed `zh%2FTW` decodes to one containing the separator.
const placeKey = (src: string, lang: string): string => JSON.stringify([lang, src])

const note = (hash: string): void => {
  const r = parse(hash)
  if (r.name === 'doc' || r.name === 'read') places.set(placeKey(r.src, r.lang), r.seg)
}

note(current)

// Reads the live `location.hash`, never an event's `newURL`: a `hashchange`
// arrives a task after the assignment that caused it, by which time a later
// write may have moved the address again, and announcing twice is harmless.
const announce = (): void => {
  current = window.location.hash
  note(current)
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

// Parsed once per address rather than once per reader: every mounted row asks
// whether it is the focused one, on every notification.
let cachedHash: string | null = null
let cachedSeg: string | null = null

const segmentOf = (hash: string): string | null => {
  if (cachedHash !== hash) {
    const r = parse(hash)
    cachedSeg = r.name === 'doc' || r.name === 'read' ? r.seg : null
    cachedHash = hash
  }
  return cachedSeg
}

/**
 * The segment the address names — the paragraph the margin is about, the row
 * the ledger keeps mounted. `null` when it names none, or names no document.
 *
 * The snapshot is a string or `null`, never an object, for the reason
 * `snapshot` above gives.
 */
export function useFocused(): string | null {
  return useSyncExternalStore(subscribe, () => segmentOf(current), () => segmentOf(current))
}

/**
 * Whether the address names this segment. A boolean snapshot, so a focus change
 * re-renders the row that lost it and the row that gained it and no other.
 */
export function useIsFocused(id: string): boolean {
  return useSyncExternalStore(subscribe, () => segmentOf(current) === id, () => segmentOf(current) === id)
}

/** Where the address last stood in a document, if it has been there this page
 *  load. See `places`. */
export const placeIn = (src: string, lang: string): string | null =>
  places.get(placeKey(src, lang)) ?? null

/**
 * Point the address at a segment of the document the caller is showing.
 *
 * **The document is named, and compared against the live address before
 * anything is written.** `go` changes `location.hash` at once and `hashchange`
 * arrives a task later, so there is a window in which a row of the document
 * being left can still take focus. Written without the check, that row's id
 * would land in the next document's address — the carried segment this module
 * exists to make unreachable, through a different door. It keeps the address's
 * own kind, so the reading view uses it as well as the ledger.
 */
export function focus(src: string, lang: string, id: string): void {
  const r = parse(window.location.hash)
  if (r.name !== 'doc' && r.name !== 'read') return
  if (r.src !== src || r.lang !== lang) return
  replace(at(r.name, src, lang, id))
}

/** Navigate. This is a history entry — the back button should undo it. */
export const go = (to: string): void => { window.location.hash = to }

/**
 * Move the address without adding a history entry.
 *
 * For view state a person did not navigate to: which paragraph they are on.
 * `hashchange` does not fire for `replaceState`, so the subscribers have to be
 * told — and **not on this tick**.
 *
 * Every caller is an event handler now; until HANDOFF-084 one was an effect,
 * where announcing synchronously would have forced React to re-render the whole
 * tree from inside a commit. The microtask stays because it keeps this path the
 * same shape as the other one: a `hashchange` from the browser is a task, so
 * React is never mid-commit when it arrives, and a route that moves itself
 * should not behave differently from one a person navigated to.
 *
 * **No effect may call this.** An effect that writes the address from some other
 * piece of state is a second writer of the segment, and two writers of one fact
 * reconciled by effects is the loop that blanked the page.
 */
export function replace(to: string): void {
  if (window.location.hash === to) return
  window.history.replaceState(null, '', to)
  queueMicrotask(announce)
}

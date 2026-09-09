/**
 * Unsaved edits, held **outside** the store on purpose.
 *
 * This is the two-tier state rule, and it is a red line rather than a
 * preference: the text a reviewer is typing never enters the store. A store
 * that re-renders on a keystroke re-renders the segment list, and a virtualized
 * list that re-renders on a keystroke is a virtualized list for nothing — on the
 * five-thousand-segment novel this workbench exists for, that is the difference
 * between typing and waiting.
 *
 * What the rest of the application is allowed to know is the *count*, which
 * changes only when a row crosses between clean and dirty. `useSyncExternalStore`
 * over `subscribe`/`count` gives the toolbar and the unload guard that number
 * without anything subscribing to the text.
 *
 * The map is keyed by segment id, and **segment ids are reassigned from `s0001`
 * on every parse**. Anything that re-parses — a re-extract, opening another
 * document — calls `clear()`, and does it *before* the fetch rather than after:
 * on a failed fetch the old entries would otherwise stay armed against ids that
 * now name different text, and `token`'s `sha1("")` hashes an absent target and
 * an empty one alike, so between two untranslated segments the lost-update token
 * cannot catch it either.
 */

const drafts = new Map<string, string>()
const listeners = new Set<() => void>()

let stamp = 0

function changed(): void {
  stamp += 1
  for (const listener of listeners) listener()
}

/** Record an edit. Returns whether this crossed the row into or out of dirty. */
export function set(id: string, text: string, original: string): boolean {
  const was = drafts.has(id)
  if (text === original) {
    if (!was) return false
    drafts.delete(id)
    changed()
    return true
  }
  drafts.set(id, text)
  if (!was) changed()
  return !was
}

export const get = (id: string): string | undefined => drafts.get(id)

export const has = (id: string): boolean => drafts.has(id)

export const ids = (): string[] => [...drafts.keys()]

export const entries = (): [string, string][] => [...drafts.entries()]

export const size = (): number => drafts.size

/** Forget the ids that were written. Everything else stays dirty and visible. */
export function forget(written: Iterable<string>): void {
  let touched = false
  for (const id of written) touched = drafts.delete(id) || touched
  if (touched) changed()
}

export function clear(): void {
  if (!drafts.size) return
  drafts.clear()
  changed()
}

// The snapshot `useSyncExternalStore` compares. It is the counter and not the
// map's size: two edits landing in one tick that net to the same size would
// otherwise be indistinguishable from no edit at all.
export const subscribe = (listener: () => void): (() => void) => {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export const version = (): number => stamp

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
 * on every parse** — so an entry means something only while the parse it was
 * typed against is the one on screen. Two things end that, and they are not the
 * same thing: the page shows a different document, which `clear()` answers at
 * the moment `doc` is replaced and not a statement earlier, and the server
 * re-parses the document, which `strand()` answers at the moment the reply
 * lands.
 *
 * It used to be one thing, cleared before the fetch, and the reason written here
 * was that the next save would otherwise post the old ids "under the new
 * address". That was not true: `save()` addresses `shown()`, which reads `doc`,
 * and `open()` does not touch `doc` until its fetch returns — so a fetch that
 * failed left the old entries pointing at the document still on screen, where
 * they were correct. What the early clear really bought was the display, and it
 * bought it a round trip too soon: `SegmentRow` reads `drafts.get(seg.id)` by id
 * alone, so what must not happen is one document's words appearing in another's
 * rows, and that becomes possible exactly when the rows change.
 *
 * Whoever leaves the document writes what is here out first — `store.open()` —
 * so nothing is discarded that could still have been written. See
 * `docs/decisions.md`, 2026-09-20.
 */

const drafts = new Map<string, string>()
const listeners = new Set<() => void>()

let stamp = 0

/**
 * Whether the ids in this map still name the paragraphs they were typed into.
 *
 * A parse reassigns ids from `s0001`, so the instant the server accepts a
 * re-extract every entry here is keyed on a number that now names some other
 * paragraph — and the lost-update token cannot catch it, because `sha1("")`
 * hashes an absent target and an empty one alike and a re-parse leaves runs of
 * untranslated segments between which the token agrees.
 *
 * The act that asked for the re-parse says so — when the document it re-parsed
 * is the one on screen, whose ids these are; since HANDOFF-088 an extract can be
 * about a file that is not — and it stays said until a parse the entries could
 * have been typed against is on screen: a re-extract reloads the whole project
 * and *then*, while the address still names the document, re-opens it, two
 * round trips with the ledger still mounted, and a keystroke arriving in between
 * would otherwise be indistinguishable from one typed against the parse that is
 * gone. A reviewer who moved on meanwhile lowers it with the document they
 * moved to. Emptying the map is not enough for the same reason.
 *
 * It is a fact about this page's own re-parse and says nothing about anybody
 * else's: `lx extract` in a terminal renumbers with no signal here at all. See
 * `docs/decisions.md`, 2026-09-20.
 */
let orphaned = false

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

/**
 * Say that a re-parse has landed, so nothing here can be written any more.
 *
 * Named by the act that causes it rather than inferred by the act that would
 * suffer from it: `open()` cannot tell a document it is re-reading after a
 * re-extract from one it is re-reading for any other reason, and guessing from
 * the address is the enumeration this project has been caught reading as a
 * definition six times over.
 */
export const strand = (): void => { orphaned = true }

export const stranded = (): boolean => orphaned

/** Forget everything, and with it the fact that a re-parse had voided it — the
 *  two go together, because what makes the map writable again is a parse on
 *  screen that the entries in it were typed against. */
export function clear(): void {
  orphaned = false
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

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

import type { StoredTarget } from './contract'

const drafts = new Map<string, string>()
const listeners = new Set<() => void>()

/**
 * The stored wording an entry lost to, per id, from the reply that refused it.
 *
 * **A refused wording stays here, in its entry, and is not sent again until the
 * reviewer chooses** — HANDOFF-091. Kept beside the entry rather than in the
 * store because it is keyed by this parse's ids exactly as the entry is, and so
 * it has to end exactly when the entry does: `clear()` ends both.
 */
const marks = new Map<string, StoredTarget>()

/**
 * The token a send of this entry must name as its `base` instead of the one
 * `doc` holds. Two writers of it: a reviewer who chose to write their wording
 * over a stored one (the token of the wording they were shown), and a write that
 * landed while the reviewer kept typing (the token that write produced — their
 * newer words are on top of it, so naming the older token would report a
 * conflict with their own write).
 */
const bases = new Map<string, string>()

/** Id → the text the save on the wire sent. Empty when nothing is on the wire. */
const flying = new Map<string, string>()

/**
 * Moves on every `clear()`. A reply that lands after the map was cleared is
 * about a parse that is no longer on screen, whose ids now name other
 * paragraphs, so it may not settle or mark anything here.
 */
let generation = 0

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

/** Drop an entry and everything kept about it. Returns whether anything went. */
function drop(id: string): boolean {
  const a = drafts.delete(id)
  const b = marks.delete(id)
  bases.delete(id)
  return a || b
}

/**
 * Record an edit. Returns whether this crossed the row into or out of dirty.
 *
 * Typing a field back to the wording it was shown ends the entry — unless a
 * save carrying the entry is on the wire. Then the reply decides: what was sent
 * will be stored, so the field's wording is no longer what is stored, and only
 * the entry can still write it.
 */
export function set(id: string, text: string, original: string): boolean {
  const was = drafts.has(id)
  if (text === original && !flying.has(id)) {
    if (!was) return false
    drop(id)
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

/** Forget ids outright — the ones a reply says name no segment. */
export function forget(ids: Iterable<string>): void {
  let touched = false
  for (const id of ids) touched = drop(id) || touched
  if (touched) changed()
}

/**
 * Settle the ids a reply wrote. **An entry goes only while it is still the text
 * that was sent**: words typed while the request was in flight are newer than
 * what was stored, and forgetting them by id — which is what this did until
 * HANDOFF-091 — left them on screen with nothing marking the row dirty and
 * nothing that would ever write them. Such an entry stays, based on the token
 * the write produced. Returns the ids that stayed.
 */
export function settle(sent: Record<string, string>, tokens: Record<string, string | undefined>): string[] {
  const kept: string[] = []
  let touched = false
  for (const [id, text] of Object.entries(sent)) {
    if (!drafts.has(id)) continue
    if (drafts.get(id) === text) {
      touched = drop(id) || touched
      continue
    }
    kept.push(id)
    marks.delete(id)
    const token = tokens[id]
    if (token) bases.set(id, token)
    else bases.delete(id)
  }
  if (touched || kept.length) changed()
  return kept
}

/**
 * The reply refused this entry: the stored wording moved underneath it. The
 * entry stays and is marked with what is stored now, and no save sends it until
 * the reviewer chooses with {@link overrule} or {@link discard}. An entry that
 * already says what is stored has converged, and goes.
 */
export function mark(id: string, stored: StoredTarget): void {
  if (!drafts.has(id)) return
  if (drafts.get(id) === stored.text) {
    drop(id)
  } else {
    marks.set(id, stored)
    bases.delete(id)
  }
  changed()
}

/** The stored wording this entry lost to, while the reviewer has not chosen. */
export const conflict = (id: string): StoredTarget | undefined => marks.get(id)

/** Every id waiting on the reviewer's choice. */
export const conflicted = (): string[] => [...marks.keys()]

/** The token a send of this entry must name, when it is not `doc`'s. */
export const base = (id: string): string | undefined => bases.get(id)

/**
 * The reviewer chose their own wording over the stored one. The next save
 * sends it as a compare-and-swap on the wording they were shown — so if the
 * stored wording has moved again since, that is a conflict again, never a
 * silent overwrite.
 */
export function overrule(id: string): void {
  const m = marks.get(id)
  if (!m) return
  bases.set(id, m.token)
  marks.delete(id)
  changed()
}

/** The reviewer chose the stored wording: their own is dropped, at their request. */
export function discard(id: string): void {
  if (drop(id)) changed()
}

/** A save is about to send these. */
export function sending(sent: Record<string, string>): void {
  for (const [id, text] of Object.entries(sent)) flying.set(id, text)
}

/** The save on the wire has an answer, or will never have one. */
export function landed(): void {
  flying.clear()
}

/** See {@link generation}. */
export const epoch = (): number => generation

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
  generation += 1
  flying.clear()
  bases.clear()
  const had = drafts.size + marks.size
  marks.clear()
  if (!had) return
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

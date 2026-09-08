/**
 * Rendering masked text.
 *
 * A placeholder is `⟦n⟧` and it stands for markup the model never sees. It is
 * drawn in lapis — the costly pigment, for what must not break — so that a
 * reviewer can see at a glance which tokens have to survive into their wording.
 *
 * **Built as React nodes, never as a string of markup.** The server sends no
 * `Content-Security-Policy` header — its header set is itself part of the frozen
 * contract — and the page it serves has unauthenticated access to every endpoint
 * including ones that spend money. The old page reached a defect here twice: a
 * filename interpolated into an unescaped attribute, and then an audit's own
 * list of unescaped sites that was short by one. JSX escapes by construction,
 * which is the fix that cannot be short by one; `dangerouslySetInnerHTML` gives
 * that property straight back.
 */
import { Fragment, type ReactNode } from 'react'

/** A `⟦n⟧` run is an atom — the same rule the sentence boundary follows. */
const PLACEHOLDER = /(⟦\d+⟧)/g

/** Masked text with its placeholders marked. */
export function Marked({ text }: { text: string }): ReactNode {
  const parts = text.split(PLACEHOLDER)
  return (
    <>
      {parts.map((part, i) =>
        // `split` with one capture group alternates literal, capture, literal…
        // so every odd index is a placeholder and no second test is needed.
        i % 2 === 1
          ? <span className="ph" key={i}>{part}</span>
          : <Fragment key={i}>{part}</Fragment>,
      )}
    </>
  )
}

/** The placeholder ids in a piece of text, in the order they appear. */
export const placeholders = (text: string): string[] =>
  text.match(/⟦\d+⟧/g) ?? []

/**
 * Whether two pieces of text carry the same placeholders, as a multiset.
 *
 * Used for **one thing only**: warning a reviewer, while they type, that their
 * wording has dropped or gained a token. It is not a gate and must never become
 * one — what a target may contain is `translate.accept`'s question and
 * `checks.py`'s, both of which run on the server, and a client answering it
 * would be a second copy of a rule this project keeps in one place. The
 * authoritative verdict arrives with the next `GET /api/doc`.
 */
export function sameSlots(a: string, b: string): boolean {
  const left = placeholders(a).sort()
  const right = placeholders(b).sort()
  return left.length === right.length && left.every((v, i) => v === right[i])
}

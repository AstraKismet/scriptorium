/**
 * The chapter, continuous.
 *
 * This is the control the maintainer asked for by name after doing ordinary work
 * on a real document: the preview was a `<dialog>` holding the whole rendered
 * chapter in one `<pre>`, easy to misclick out of and unable to cope with a long
 * chapter. Reviewing a novel is the primary use case and prose flow is exactly
 * what a translation is judged on, so this is a page rather than a modal.
 *
 * **It is deliberately not editable, and that is not an omission.** The text
 * here is the *rendered* document: placeholders are gone and real markup is
 * back, against the map each wording's ids actually mean. It is neither
 * `segment.source` nor `segment.target`, and no endpoint on this surface accepts
 * it as a target — a field here would be a field whose contents cannot be saved.
 * Clicking a paragraph takes you to it in the ledger, where the thing on screen
 * is the thing that gets written.
 *
 * **The sentence rule is Python's and this computes none of it.**
 * `POST /api/sentences` answers an ordered array of *strings* whose
 * concatenation is the input exactly, so the pieces are rendered in order and no
 * cursor arithmetic happens here — which matters because two sentences in one
 * paragraph may be byte-identical and a client locating them by search would put
 * both marks on the first. Its known failure is visible here and is the rule
 * working as specified: Chinese dialogue attribution over-splits, so
 * `「站住！」他喊。沒有人停下。` shows `他喊。` as a piece of its own.
 */
import { useEffect, useState } from 'react'

import * as api from '../api'
import * as routes from '../router'
import { isError, type Block, type PreviewResponse } from '../contract'
import { useStore } from '../store'

export function Reading({ seg }: { seg: string | null }) {
  const doc = useStore(s => s.doc)
  const say = useStore(s => s.say)
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [error, setError] = useState('')
  const [current, setCurrent] = useState<string | null>(seg)
  const [pieces, setPieces] = useState<string[] | null>(null)

  const src = doc?.source ?? ''
  const lang = doc?.lang ?? ''

  useEffect(() => {
    if (!src) return
    let live = true
    setPreview(null)
    setError('')
    // `blocks` roughly doubles this reply, because the top-level `text` is the
    // same document a second time and there is no parameter to switch either
    // off. Dropping `text` is candidate cargo for the next version bump; until
    // then this pays for it once per chapter opened, which is the right place
    // for that cost compared with a per-block request.
    api.getPreview({ src, lang })
      .then(p => { if (live) setPreview(p) })
      .catch((e: unknown) => { if (live) setError(String(e)) })
    return () => { live = false }
  }, [src, lang])

  // The pieces of the paragraph the reviewer is on, and only that one. Asking
  // for the whole chapter was considered and refused on the wire for the same
  // reason it is refused here: it triples the document's size to answer a
  // question about one paragraph.
  useEffect(() => {
    const block = preview?.blocks.find(b => b.id === current)
    if (!block) { setPieces(null); return }
    let live = true
    api.postSentences({ texts: [block.text] })
      .then(r => { if (live) setPieces(r.sentences[0] ?? null) })
      .catch(() => { if (live) setPieces(null) })
    return () => { live = false }
  }, [preview, current])

  // A deep link lands on a paragraph. Scrolled once, on arrival, rather than
  // whenever `current` moves — a click should not yank the page under the
  // pointer that made it.
  useEffect(() => {
    if (!seg || !preview) return
    document.getElementById(`b-${seg}`)?.scrollIntoView({ block: 'center' })
  }, [seg, preview])

  if (!doc) return null

  const issues = new Map(doc.segments.map(s => [s.id, s.issues]))

  return (
    <>
      <div className="bar">
        <button type="button" onClick={() => { routes.go(routes.doc(src, lang, current)) }}>
          ← Back to the ledger
        </button>
        <span className="sep" />
        <span className="reading-bar">
          {doc.source} · {doc.lang}
          {preview && ` · ${preview.missing} without a usable translation`}
          {preview && ` · writes to ${preview.default_out}`}
        </span>
        <span className="spacer" />
        <span className="note-line" style={{ maxWidth: 'none' }}>
          {/* `fallback` is hardcoded true on this endpoint and defaults false on
              the one that writes the file, so the same document previews and
              renders differently for any segment counted in `missing`. Said out
              loud rather than left to be discovered. */}
          untranslated paragraphs show their source here; the written file shows a marker
        </span>
      </div>

      <div className="reading">
        {error && <div className="empty-page"><h3>Could not read it</h3><p>{error}</p></div>}
        {!error && !preview && <div className="empty-page"><p>rendering…</p></div>}
        {preview && (
          <div className="page">
            {preview.blocks.map((block, i) => (
              <Paragraph
                key={block.id ?? `k${i}`}
                block={block}
                failing={(issues.get(block.id ?? '') ?? []).some(isError)}
                current={block.id != null && block.id === current}
                pieces={block.id != null && block.id === current ? pieces : null}
                onPick={() => {
                  if (block.id == null) return
                  setCurrent(block.id)
                  routes.replace(routes.read(src, lang, block.id))
                }}
                onOpen={() => {
                  if (block.id == null) return
                  say(`opening ${block.id} in the ledger`)
                  routes.go(routes.doc(src, lang, block.id))
                }}
              />
            ))}
          </div>
        )}
      </div>
    </>
  )
}

function Paragraph({ block, failing, current, pieces, onPick, onOpen }: {
  block: Block
  failing: boolean
  current: boolean
  pieces: string[] | null
  onPick: () => void
  onOpen: () => void
}) {
  // A run of skeleton the pipeline did not translate — `id === null` is the
  // discriminator, and there is no `type` tag. It is the document's own
  // punctuation and markup, so it is shown rather than swallowed: dropping it
  // would be this page deciding what the document says.
  if (block.id == null) {
    return <span className="block skeleton">{block.text}</span>
  }

  // `from` names the branch that produced the text. Since version 4 a stored
  // translation does not always take `target`: a wording whose placeholders
  // cannot be substituted without malforming the document answers `source` like
  // a segment nobody wrote. So this marks "not the translation" and the ledger,
  // joined on the id, says which of the two it is.
  const untranslated = block.from !== 'target'

  return (
    <span
      id={`b-${block.id}`}
      className={[
        'block', 'seg',
        block.kind === 'heading' ? 'heading' : '',
        untranslated ? 'untranslated' : '',
        failing ? 'failing' : '',
      ].filter(Boolean).join(' ')}
      style={block.kind === 'heading' ? { fontSize: '1.35em', letterSpacing: '.02em' } : undefined}
      role="button"
      tabIndex={0}
      aria-current={current}
      title={
        (untranslated ? 'no usable translation — showing the source. ' : '') +
        (failing ? 'this segment fails a check. ' : '') +
        `${block.id} · double-click or press Enter to edit it in the ledger`
      }
      onClick={onPick}
      onDoubleClick={onOpen}
      onKeyDown={e => {
        if (e.key === 'Enter') { e.preventDefault(); onOpen() }
        if (e.key === ' ') { e.preventDefault(); onPick() }
      }}
    >
      {pieces
        // Rendered in order, never located by searching: the concatenation is
        // the input exactly, so the pieces *are* the positions.
        ? pieces.map((piece, i) => <span className="sentence" key={i}>{piece}</span>)
        : block.text}
    </span>
  )
}

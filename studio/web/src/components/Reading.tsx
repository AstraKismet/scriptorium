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
 *
 * **It is virtualized, by line, since HANDOFF-090.** A five-thousand-segment
 * document rendered here in one pass was 9999 `.block` spans and about 10 000
 * DOM nodes, and the maintainer measured *Read → click → Back* freezing the
 * whole browser for about ten seconds in a painted tab. The unit `virtua` lays
 * out is a {@link lines line}: the blocks between two line breaks of the
 * document's own skeleton, so each item is a block-level box and the prose still
 * flows inside it. What that costs is the browser's own find-in-page, which can
 * only search what is mounted — the ledger's filter is the search that works at
 * this scale. `docs/decisions.md`, 2026-10-31.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { VList } from 'virtua'

import * as api from '../api'
import * as routes from '../router'
import { isError, type Block, type PreviewResponse } from '../contract'
import { useStore } from '../store'

/** One piece of one block, as drawn on one line of the reading view. */
export interface Piece {
  block: Block
  /** The block's text, or the half of a skeleton run a line break divided. */
  text: string
  /** The block's position in `preview.blocks`, which is what keys it. */
  index: number
}

/**
 * The document's blocks, cut into the lines its own skeleton already draws.
 *
 * A line ends at the first line break of a skeleton run: the run's text up to
 * and including that break closes the line it is on, and what follows the break
 * opens the next. **Nothing is added, dropped or reordered** — the pieces of
 * every line, concatenated in order, are `preview.text` byte for byte — and a
 * segment's own text is never divided, interior line breaks included, because a
 * segment is the unit a click names.
 *
 * It draws what the unbroken flow drew: in `white-space: pre-wrap` a break at
 * the very end of a block-level box draws no empty line after it, and one at the
 * very start draws one before it, so `para\n\npara` cut as `para\n` | `\npara`
 * is the one blank line it always was. Measured by the review of HANDOFF-090 in
 * headless Chrome, 846 renders at two widths, every visible character in the
 * same place. Cutting at *every* break measured the same too; the first break is
 * kept because it makes fewer lines, not because the other draws differently —
 * an earlier version of this comment claimed it did, and the measurement said
 * otherwise. A skeleton run with no break (a list marker, a run between two
 * inline segments, a table's ` | `) stays on the line it is on, which is what
 * keeps `- ` next to its item.
 */
export function lines(blocks: readonly Block[]): Piece[][] {
  const out: Piece[][] = []
  let line: Piece[] = []
  blocks.forEach((block, index) => {
    const at = block.id == null ? block.text.indexOf('\n') : -1
    if (at < 0) {
      line.push({ block, text: block.text, index })
      return
    }
    line.push({ block, text: block.text.slice(0, at + 1), index })
    out.push(line)
    const rest = block.text.slice(at + 1)
    line = rest ? [{ block, text: rest, index }] : []
  })
  if (line.length) out.push(line)
  return out
}

export function Reading() {
  const doc = useStore(s => s.doc)
  const say = useStore(s => s.say)
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [error, setError] = useState('')
  // The sentences of one paragraph, **with the id they belong to**. Held bare,
  // they were handed to whichever paragraph was current, so moving the address
  // drew the new paragraph in the old one's sentences until its own request
  // answered (found by HANDOFF-090's mutation pass; older than it).
  const [split, setSplit] = useState<{ id: string; pieces: string[] } | null>(null)
  // The paragraph the reviewer is on is the address's, here as in the ledger —
  // never a copy seeded from it on mount. A copy is what let this view keep
  // highlighting, and send Back to, a paragraph the address no longer named.
  const current = routes.useFocused()
  const pieces = split && split.id === current ? split.pieces : null
  const landed = useRef('')

  const src = doc?.source ?? ''
  const lang = doc?.lang ?? ''

  const rows = useMemo(() => (preview ? lines(preview.blocks) : []), [preview])
  // Which line each segment is drawn on, so landing and `keepMounted` are a
  // lookup rather than a scan of every line per render.
  const lineOf = useMemo(() => {
    const m = new Map<string, number>()
    rows.forEach((line, i) => { for (const p of line) if (p.block.id != null) m.set(p.block.id, i) })
    return m
  }, [rows])
  // The line keyboard focus is in, tracked on its own: Tab moves focus and not
  // the address, so focus can sit on a line the address does not name.
  const [focusedLine, setFocusedLine] = useState<number | null>(null)
  // Two lines stay mounted wherever the scroll goes, as the ledger keeps its
  // row: the address's, which carries the sentence marks, and the one holding
  // keyboard focus. Without the second, a PageDown from a paragraph reached by
  // Tab unmounted the focused element, focus fell to `body`, and keyboard
  // scrolling stopped — measured by HANDOFF-090's review, in headless Chrome.
  const keep = useMemo(() => {
    const at = current == null ? undefined : lineOf.get(current)
    const out = at == null ? [] : [at]
    if (focusedLine != null && focusedLine !== at && focusedLine < rows.length) out.push(focusedLine)
    return out
  }, [lineOf, current, focusedLine, rows.length])

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
    if (!block || block.id == null) { setSplit(null); return }
    const id = block.id
    let live = true
    api.postSentences({ texts: [block.text] })
      .then(r => { if (live) setSplit(r.sentences[0] ? { id, pieces: r.sentences[0] } : null) })
      .catch(() => { if (live) setSplit(null) })
    return () => { live = false }
  }, [preview, current])

  // A deep link lands on a paragraph. Scrolled once, on arrival, rather than
  // whenever `current` moves — a click should not yank the page under the
  // pointer that made it. **Once per document, remembered in a ref**, because a
  // click moves the address and the address is a dependency here: keyed on it
  // alone, every click re-centred the page, which is what this view did until
  // HANDOFF-084 measured it (a paragraph near the bottom of the window, clicked,
  // moved 517 px).
  //
  // **The paragraph is centred, not its line, and the virtualizer is not asked
  // to scroll at all.** The line the address names is in `keepMounted`, so it is
  // mounted at its estimated position from the first render, and two frames
  // later — once it has been measured — the element itself is scrolled into the
  // middle; `virtua` keeps it there as the lines around it are measured. Asking
  // `scrollToIndex` to centre the line was the first version: it centres the
  // unit it virtualizes, so a line taller than the window — a Markdown table
  // row, every cell a segment joined by ` | ` — put the paragraph 1500 px below
  // the bottom of it (HANDOFF-090's review, T4), and centring the element after
  // it did not help, because `scrollToIndex` goes on correcting towards its own
  // target as lines are measured and undid it. Measured in headless Chrome: the
  // table cell lands in the middle of the window, an ordinary paragraph within
  // 30 px of it.
  useEffect(() => {
    if (!preview) return
    const key = `${src} ${lang}`
    if (landed.current === key) return
    landed.current = key
    const i = current == null ? undefined : lineOf.get(current)
    if (i == null || current == null) return
    let frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => {
        document.getElementById(`b-${current}`)?.scrollIntoView({ block: 'center' })
      })
    })
    return () => { cancelAnimationFrame(frame) }
  }, [preview, current, src, lang, lineOf])

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

      {error && <div className="reading"><div className="empty-page"><h3>Could not read it</h3><p>{error}</p></div></div>}
      {!error && !preview && <div className="reading"><div className="empty-page"><p>rendering…</p></div></div>}
      {preview && (
        <VList
          className="reading"
          data={rows}
          keepMounted={keep}
          // No padding on the scroller, at either end. `virtua` sizes its
          // viewport from the scroller's *content* box, so padding there shrinks
          // the range it mounts: measured on HANDOFF-090 with the old page's 40vh
          // run-out on this element, the lines stopped 160 px above the bottom of
          // an 818 px window and a scroll did not fill them in. The page's top
          // margin and its run-out are the first and the last line's instead.
        >
          {(line, i) => (
            <Line
              key={i}
              line={line}
              first={i === 0}
              last={i === rows.length - 1}
              src={src}
              lang={lang}
              current={current}
              pieces={pieces}
              failing={id => (issues.get(id) ?? []).some(isError)}
              say={say}
              onFocusIn={() => { setFocusedLine(i) }}
            />
          )}
        </VList>
      )}
    </>
  )
}

/**
 * One line of the chapter. **Exported so a test can click a paragraph without
 * mocking the virtualizer's measurement**, which jsdom cannot answer: it lays
 * nothing out, so `VList` mounts only the line `keepMounted` names. The ledger's
 * rows are tested beside the application for the same reason, and this is the
 * same component with the same handlers the list draws.
 */
export function Line({ line, first, last = false, src, lang, current, pieces, failing, say, onFocusIn }: {
  line: readonly Piece[]
  /** Carries the page's top margin. */
  first: boolean
  /** Carries the long run-out, so a chapter's end is read clear of the window's bottom. */
  last?: boolean
  src: string
  lang: string
  current: string | null
  pieces: string[] | null
  failing: (id: string) => boolean
  say: (text: string) => void
  /** Keyboard focus entered this line; the list keeps it mounted. */
  onFocusIn?: () => void
}) {
  return (
    <div
      className="line"
      style={first || last ? { paddingTop: first ? 48 : undefined, paddingBottom: last ? '40vh' : undefined } : undefined}
      // React's `onFocus` bubbles, so this hears focus landing on any paragraph
      // of the line.
      onFocus={onFocusIn}
    >
      {line.map(piece => {
        const id = piece.block.id
        return (
          <Paragraph
            // A block is on a line once: a skeleton run a break divides puts
            // one half on each of two lines, never two halves on one.
            key={piece.index}
            block={piece.block}
            text={piece.text}
            failing={id != null && failing(id)}
            current={id != null && id === current}
            pieces={id != null && id === current ? pieces : null}
            onPick={() => {
              if (id == null) return
              routes.focus(src, lang, id)
            }}
            onOpen={() => {
              if (id == null) return
              say(`opening ${id} in the ledger`)
              routes.go(routes.doc(src, lang, id))
            }}
          />
        )
      })}
    </div>
  )
}

function Paragraph({ block, text, failing, current, pieces, onPick, onOpen }: {
  block: Block
  /** What this line draws of it: all of a segment, or one side of a skeleton run's break. */
  text: string
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
    return <span className="block skeleton">{text}</span>
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
        : text}
    </span>
  )
}

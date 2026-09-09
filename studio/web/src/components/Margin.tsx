/**
 * The margin: what the model was told about this book's voice, and what the
 * memory already holds for a sentence nearly like this one.
 *
 * Both halves are the answer to a question a reviewer could not previously ask.
 * A translation reads as it does partly because of a style sheet the reviewer
 * wrote weeks ago and cannot see from here, and partly because the memory
 * answers exactly or not at all — the second chapter says *She had not slept*
 * where the first said *She had not slept well*, and an exact-key lookup offers
 * nothing.
 *
 * ⚠️ **There is deliberately no apply control on a suggestion, and one must not
 * be added.** A fuzzy hit differs in its placeholder set by definition, so
 * wording lifted from one segment into another renders a bare `⟦2⟧`. Automatic
 * application of fuzzy matches is deferred indefinitely in `AGENTS.md`'s *Not in
 * scope*. A reviewer who wants those words retypes them, and they are then their
 * own words, seen by `translate.accept` and the origin rules like any other
 * human write.
 *
 * **The two halves have opposite fetch economics and are not fetched together.**
 * The style answer is a configuration read and is cheap, so it follows the
 * cursor. A suggestion is a quadratic comparison against the whole memory —
 * about 1.3 s at the median for one segment and 2.1 s at p90, spent on the
 * machine running the workbench — so it is asked for automatically only where a
 * near match is what a reviewer is actually short of: a segment with no wording
 * yet, or one that is failing. Everywhere else it is a button. Reading a chapter
 * of finished prose should not cost a second and a half of CPU per paragraph
 * scrolled past.
 */
import { useEffect, useState } from 'react'

import * as api from '../api'
import { useStore } from '../store'
import { isError, type StyleResponse, type SuggestResponse } from '../contract'

/**
 * Long enough that arrowing down a chapter does not fire a request per row.
 *
 * It is 700 rather than something friendlier because of what the *server* pays:
 * `POST /api/style` and `POST /api/suggest` each call `store.load_doc` before
 * they begin, which reads every skeleton node and every segment row of the whole
 * document. On a five-thousand-segment novel a margin that refetched on every
 * focus change would be a full document read per paragraph looked at.
 */
const SETTLE = 700

/**
 * What the style margin already answered, for this document.
 *
 * The answer depends on the segment's own text and on a style sheet that does
 * not move while a page is open, so a second look at a paragraph is free.
 * Cleared when the document changes, because ids are reassigned from `s0001` on
 * every parse and a cached answer keyed on one would then be about other text.
 */
let styleCache = new Map<string, StyleResponse>()
let cachedFor = ''

interface Margin {
  style: StyleResponse | null
  suggest: SuggestResponse | null
  loading: boolean
  error: string
}

const EMPTY: Margin = { style: null, suggest: null, loading: false, error: '' }

export function Margin() {
  const doc = useStore(s => s.doc)
  const focused = useStore(s => s.focused)
  const [margin, setMargin] = useState<Margin>(EMPTY)
  const [asked, setAsked] = useState(0)

  const src = doc?.source ?? ''
  const lang = doc?.lang ?? ''
  const seg = doc?.segments.find(s => s.id === focused) ?? null
  // Where a near match earns its cost without being asked for.
  const wanted = !!seg && (!seg.target || seg.issues.some(isError))

  useEffect(() => {
    if (!src || !focused) { setMargin(EMPTY); return }
    const scope = `${src}|${lang}`
    if (cachedFor !== scope) { styleCache = new Map(); cachedFor = scope }
    const cached = styleCache.get(focused)
    if (cached) { setMargin({ ...EMPTY, style: cached }); return }
    let live = true
    setMargin(EMPTY)
    const timer = setTimeout(() => {
      void api.postStyle({ src, lang, ids: [focused] })
        .then(style => {
          styleCache.set(focused, style)
          if (live) setMargin(m => ({ ...m, style }))
        })
        .catch((e: unknown) => { if (live) setMargin(m => ({ ...m, error: String(e) })) })
    }, SETTLE)
    // The sequence token this surface needs three times over, spelled as a
    // cleanup: a reply that arrives after the reviewer has moved on belongs to a
    // segment nobody is looking at, and the last reply to *arrive* would
    // otherwise win.
    return () => { live = false; clearTimeout(timer) }
  }, [src, lang, focused])

  useEffect(() => {
    if (!src || !focused) return
    if (!wanted && asked === 0) return
    let live = true
    setMargin(m => ({ ...m, loading: true, suggest: null }))
    const timer = setTimeout(() => {
      void api.postSuggest({ src, lang, ids: [focused], most: 5 })
        .then(suggest => { if (live) setMargin(m => ({ ...m, suggest, loading: false })) })
        .catch((e: unknown) => { if (live) setMargin(m => ({ ...m, loading: false, error: String(e) })) })
    }, SETTLE)
    return () => { live = false; clearTimeout(timer) }
  }, [src, lang, focused, wanted, asked])

  // A press belongs to the segment it was made on. Without this the counter
  // would keep a later segment's panel open because an earlier one was asked
  // about, which is the same stale-reply shape one layer up.
  useEffect(() => { setAsked(0) }, [focused])

  if (!doc) return null

  if (!focused) {
    return (
      <div className="margin">
        <h3>Margin</h3>
        <div className="body">
          <p className="empty">
            Choose a segment. This shows what the model was told about the voice,
            and wording already banked for a source nearly like this one.
          </p>
        </div>
      </div>
    )
  }

  const hits = margin.suggest?.segments[0]

  return (
    <div className="margin">
      <h3>{focused}</h3>

      <section>
        <div className="body">
          <div className="block-names" style={{ marginBottom: 6 }}>What the model is told</div>
          {margin.style
            ? (
              <>
                {/*
                  Collapsed, and only this half. The always-on part is the target
                  language's register brief plus the style sheet's preamble: it is
                  document-static, it is the same string on every request of a run,
                  and it is forty lines long. Left open it pushes the two things
                  that *change* with the cursor — the scene's own blocks and the
                  memory's near matches — off the bottom of the panel, which is
                  what it did on the first run of this page.
                */}
                <details>
                  <summary style={{ cursor: 'pointer', color: 'var(--muted)', fontSize: 12 }}>
                    the standing brief — same for every request of a run
                  </summary>
                  <p className="voice">{margin.style.voice || '(no brief and no style sheet)'}</p>
                </details>
                {margin.style.voice_notes.map((note, i) => (
                  <div key={i} style={{ marginTop: 10 }}>
                    <div className="block-names">{note.names.join(', ')}</div>
                    <p className="voice">{note.notes}</p>
                  </div>
                ))}
                {margin.style.voice_notes.length === 0 && (
                  <p className="caveat" style={{ color: 'var(--faint)' }}>
                    No style block names anything in this segment. A run sends a whole
                    batch — a scene — so a block naming a character who speaks a few
                    paragraphs away is sent with it and is not shown here.
                  </p>
                )}
              </>
            )
            : <p className="empty">{margin.error ? margin.error : 'reading the style sheet…'}</p>}
        </div>
      </section>

      <section>
        <div className="body">
          <div className="block-names" style={{ marginBottom: 6 }}>
            Near matches from the memory
          </div>
          {!wanted && asked === 0 && !margin.loading && !margin.suggest && (
            <p className="empty">
              This segment is translated and passing, so the memory was not searched.
              Comparing one segment against a whole book&rsquo;s memory takes a second or
              two of this machine&rsquo;s own time.{' '}
              <button type="button" className="quiet" onClick={() => { setAsked(n => n + 1) }}>
                Look anyway
              </button>
            </p>
          )}
          {margin.loading && <p className="empty">comparing against the memory…</p>}
          {!margin.loading && hits && hits.suggestions.length === 0 && (
            <p className="empty">
              Nothing above {margin.suggest?.cutoff} in {margin.suggest?.records} banked lines.
            </p>
          )}
          {hits?.suggestions.map((hit, i) => (
            <div className="hit" key={i}>
              <div>
                <span className="score">{hit.score.toFixed(2)}</span>{' '}
                <span className="keys">
                  {[hit.context, hit.tone, hit.variant].filter(Boolean).join(' · ') || 'no key axes'}
                  {hit.waived ? ' · waived where it was banked' : ''}
                </span>
              </div>
              <p className="was">{hit.source}</p>
              <p className="now">{hit.target}</p>
            </div>
          ))}
          {hits?.truncated && (
            <p className="caveat">
              A work budget stopped this search before the memory ran out — there may
              be better matches that were never compared.
            </p>
          )}
          {margin.suggest && (
            <p className="caveat" style={{ color: 'var(--faint)' }}>
              Advisory. A near match differs in its placeholders by definition, so there
              is no apply here on purpose — retype what you want and it is your wording.
              Scored by {margin.suggest.algorithm}.
            </p>
          )}
        </div>
      </section>
    </div>
  )
}

/**
 * The left rail: what this project holds, and what it could.
 *
 * **It is never disabled while a run is in flight.** The old page's `busy()`
 * swept the toolbar and the ledger and left the rail alone, and that was
 * recorded as a known hole — a document could be switched mid-run. The rebuild
 * does not close it by disabling the rail: switching document during a run is a
 * reasonable thing to want, and the run is already addressed to a snapshot taken
 * at dispatch, so it finishes against the document it started on and says which
 * one when that is no longer the one on screen.
 *
 * **Every entry is a link, and does nothing but move the address** — the *Not
 * yet extracted* ones included, since HANDOFF-088. The page a link lands on is
 * where acts live; for a file nobody has extracted, that page offers the
 * extract.
 */
import { useMemo } from 'react'

import { useStore } from '../store'
import * as routes from '../router'

/**
 * Chapter order, not byte order.
 *
 * The wire hands these back sorted by the flattened document identity, so
 * `book/ch1.md` < `book/ch10.md` < `book/ch2.md` — and a novel is twenty
 * chapters, so a reader reaches the end of one and comes back to a list in the
 * wrong order twenty times. Sorting for **display** is the client's own business
 * and no rule of the pipeline's: nothing here decides what a document *is*, only
 * which line it is drawn on.
 */
const byChapter = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

export function Rail() {
  const state = useStore(s => s.state)
  const at = useStore(s => s.at)
  const doc = useStore(s => s.doc)

  const docs = useMemo(
    () => [...(state?.docs ?? [])].sort((a, b) =>
      byChapter.compare(a.source, b.source) || byChapter.compare(a.lang, b.lang)),
    [state],
  )
  const untracked = useMemo(
    () => [...(state?.untracked ?? [])].sort((a, b) =>
      byChapter.compare(a.source, b.source) || byChapter.compare(a.lang, b.lang)),
    [state],
  )

  if (!state) return <aside />

  const current = (src: string, lang: string) =>
    at != null && at.src === src && at.lang === lang

  return (
    <aside>
      <div className="brand">
        <h1>Scriptorium</h1>
        <p>workbench · v{state.version}</p>
      </div>

      <div className="rail">
        <h2>Tracked</h2>
        {docs.length === 0 && <p className="none">None yet.</p>}
        {docs.map(d => {
          // The open document's own numbers, live. `GET /api/state` loads every
          // segment of every document in the project to compute these, so it is
          // not called after a run — and the document on screen already knows
          // what it holds.
          const mine = current(d.source, d.lang) && doc
          const done = mine ? doc.report.translated : d.done
          const total = mine ? doc.report.segments : d.total
          return (
            <button
              key={d.source + ' ' + d.lang}
              type="button"
              className="doc"
              aria-current={current(d.source, d.lang)}
              // Back to the paragraph the address last named in *this* document,
              // if it has named one — never the one on screen, which belongs to
              // another document whose ids restart at `s0001` too.
              onClick={() => { routes.go(routes.doc(d.source, d.lang, routes.placeIn(d.source, d.lang))) }}
            >
              <b>{d.source}</b>
              <small>{d.lang} · {done}/{total}</small>
              <span className="meter">
                <i style={{ width: `${Math.round((100 * done) / Math.max(total, 1))}%` }} />
              </span>
            </button>
          )
        })}

        <h2>Not yet extracted</h2>
        {untracked.length === 0 && (
          <p className="none">Nothing new matches <code>sources</code>.</p>
        )}
        {untracked.map(c => (
          <button
            key={c.source + ' ' + c.lang}
            type="button"
            className="doc"
            aria-current={current(c.source, c.lang)}
            // **A link, like every entry above it.** This entry used to be the
            // extract itself: it called `open()` beside the address and then
            // extracted whatever was on screen, and `App.tsx`'s effect put the
            // addressed document back a render later — so the one request it sent
            // re-parsed the document that was open while the log named the file
            // that was clicked (HANDOFF-088).
            //
            // *Lost:* extracting here and then navigating, which kept the act to
            // one click. Every defect the review of it found came from the page
            // moving itself after a delay the reviewer did not choose: text typed
            // while that navigation wrote the open document out was dropped with
            // no line, a reviewer who had left and come back by Back was pulled
            // away, and a list read at startup offered a file a terminal had since
            // extracted and translated, so the click was a re-extract nobody
            // confirmed. A link moves the address when the reviewer asks and at no
            // other time, and the page it lands on reads the file before anything
            // is offered — so a stale entry opens the document instead.
            onClick={() => { routes.go(routes.doc(c.source, c.lang, routes.placeIn(c.source, c.lang))) }}
          >
            <b>{c.source}</b>
            <small>{c.lang} · not extracted</small>
          </button>
        ))}
      </div>

      <div className="links">
        <a href={routes.backends()}>Backends</a>
        <a href={routes.routing()}>Routing</a>
      </div>
      <footer>{state.cwd}</footer>
    </aside>
  )
}

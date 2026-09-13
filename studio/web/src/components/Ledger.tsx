/**
 * The ledger: every segment the filter admits, virtualized.
 *
 * Virtualization is why this rebuild exists at all. A five-thousand-segment
 * novel is the sizing case, each row carries a textarea and a marginalia block,
 * and the page it replaces rebuilt the whole list with `innerHTML =` on every
 * repaint. `virtua` measures rows as they mount rather than being told a height,
 * which is what lets a row grow with its wording and with the findings hanging
 * under it.
 *
 * **A row that scrolls out of view unmounts, and that is safe here only because
 * unsaved text lives outside React.** `drafts` is a module-level map; a row
 * remounting reads its own entry back as the field's `defaultValue`. The caret
 * is lost and the words are not — and the row being typed in is kept mounted
 * regardless, so the ordinary case never reaches that path.
 *
 * ⚠️ **Everything here depends on the browser producing frames**, because the
 * virtualizer measures with a `ResizeObserver` and those are delivered as part
 * of the rendering steps. A tab that is not being rendered — hidden, occluded,
 * in the background — delivers none, so the list mounts, measures a viewport of
 * zero, renders no rows, and stays that way with no error anywhere. That is a
 * property of the platform and not something to guard against in a page nobody
 * is looking at; it is written down because it costs an afternoon to diagnose
 * from the inside, where it is indistinguishable from a bug in this file.
 * `document.visibilityState` is the tell.
 */
import { useEffect, useMemo, useRef } from 'react'
import { VList, type VListHandle } from 'virtua'

import { SegmentRow } from './SegmentRow'
import * as routes from '../router'
import { useStore, visible } from '../store'

export function Ledger() {
  const doc = useStore(s => s.doc)
  const filter = useStore(s => s.filter)
  const focused = routes.useFocused()
  const list = useRef<VListHandle>(null)
  const landed = useRef('')

  const rows = useMemo(() => visible(doc, filter), [doc, filter])

  // Keep the row a reviewer is in mounted whatever the scroll does. Without it a
  // long paragraph pushing itself off the top of the viewport takes its own
  // textarea with it mid-sentence.
  const keep = useMemo(() => {
    const i = rows.findIndex(s => s.id === focused)
    return i < 0 ? [] : [i]
  }, [rows, focused])

  /**
   * Land on the paragraph the address names, once per document opened.
   *
   * A deep link, a reload and the return trip from the reading view all arrive
   * with a segment in the address, and a five-thousand-segment novel opened at
   * the top has lost the reviewer's place. It fires once per document rather
   * than whenever the focus moves, because scrolling the list under the click
   * that moved the focus is how a page fights the person using it.
   */
  useEffect(() => {
    if (!doc || !focused) return
    const key = `${doc.source} ${doc.lang}`
    if (landed.current === key) return
    const i = rows.findIndex(s => s.id === focused)
    if (i < 0) return
    landed.current = key
    list.current?.scrollToIndex(i, { align: 'center' })
  }, [doc, rows, focused])

  if (!doc) return null

  if (!rows.length) {
    return (
      <div className="ledger">
        <div className="empty-page">
          <h3>Nothing here</h3>
          <p>No segments match this filter.</p>
        </div>
      </div>
    )
  }

  return (
    <VList
      ref={list}
      className="ledger"
      data={rows}
      keepMounted={keep}
      // The list ends a long way above the bottom of the window, so the last
      // paragraph of a chapter can be read and edited without sitting on the
      // rule. A scriptorium leaves a margin.
      style={{ paddingBottom: '30vh' }}
    >
      {seg => <SegmentRow key={seg.id} seg={seg} />}
    </VList>
  )
}

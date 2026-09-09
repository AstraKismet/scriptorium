/**
 * The bar. Every act that addresses the whole document.
 *
 * Read in pipeline order after the run buttons — read the source again, check
 * what came out, read it, write it, bank it — because that is the order a
 * chapter goes through and a reviewer should not have to remember which button
 * comes next.
 *
 * **One run at a time**, and the flag is read here rather than by a sweep over
 * whatever controls happen to exist. The old page disabled `.bar button` and
 * `.ledger button` by query, which missed the `<select>` beside them: a filter
 * change mid-run repainted a ledger full of freshly enabled per-row controls.
 * A flag every control reads cannot be short by one.
 */
import { useEffect, useState, useSyncExternalStore } from 'react'

import { ask } from './Confirm'
import { ModelPicker } from './ModelPicker'
import * as drafts from '../drafts'
import * as routes from '../router'
import { useStore, type Filter } from '../store'

const BOUNDS = [0, 10, 25, 50, 100]

export function Toolbar() {
  const doc = useStore(s => s.doc)
  const running = useStore(s => s.running)
  const settled = useStore(s => s.settled)
  const limit = useStore(s => s.limit)
  const setLimit = useStore(s => s.setLimit)
  const filter = useStore(s => s.filter)
  const setFilter = useStore(s => s.setFilter)
  const runJob = useStore(s => s.runJob)
  const check = useStore(s => s.check)
  const render = useStore(s => s.render)
  const commit = useStore(s => s.commit)
  const save = useStore(s => s.save)
  const refresh = useStore(s => s.refresh)
  const extract = useStore(s => s.extract)
  const startOver = useStore(s => s.startOver)
  const say = useStore(s => s.say)

  const dirty = useSyncExternalStore(drafts.subscribe, drafts.version)
  const [startingOver, setStartingOver] = useState(false)

  // The unsaved-work guard. `beforeUnload` is the browser's only hook and it
  // cannot say what is unsaved — but it is the difference between closing a tab
  // and losing an afternoon's wording.
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => { if (drafts.size()) e.preventDefault() }
    window.addEventListener('beforeunload', guard)
    return () => { window.removeEventListener('beforeunload', guard) }
  }, [])

  if (!doc) return null

  const ready = settled() && !running
  const report = doc.report

  /** Re-read the source. Destructive in a way the wire never names. */
  const reExtract = async () => {
    if (!ready) return
    // Before the dialog, not after: an edit still in the ledger is written
    // against the ids *this* parse produced, and the next parse reassigns them
    // from `s0001`.
    await save()
    // **What `save()` could not write, this must not discard.** A refusal keeps
    // the entry in `drafts`, and re-extracting then clears it — and those edits
    // can never be applied afterwards, because the ids they are keyed on will
    // name different text. So the act stops here instead, with the reviewer
    // holding the only copy.
    if (drafts.size()) {
      say(
        `  ${drafts.ids().slice(0, 20).join(', ')} could not be saved, and a re-extract ` +
        `renumbers segments — copy that wording somewhere before trying again`,
        'bad',
      )
      return
    }
    // **Re-read before deciding what to warn about.** `report.translated` is a
    // client snapshot, and `save()` refreshes it only when it had something to
    // send — so a book translated by `lx run` in a terminal while this page sat
    // open still reported 0 here, and the confirmation this act needs would have
    // been skipped. One extra read on a deliberate, rare, destructive press.
    await refresh()
    const now = useStore.getState().doc
    // **Asked only when there is something to lose.** On a document with nothing
    // translated a re-extract cannot discard a translation, and it cannot drop a
    // hold either — holding requires a non-empty target. A dialog there would be
    // ceremony over an act with no cost, and one that fires every time teaches
    // people to click through the one that matters.
    const ok = !now?.report.translated || await ask(
      `Re-extract ${doc.source}?`,
      [
        'The source file is read again and the document re-parsed.',
        '',
        'A paragraph whose text has not changed, and has not moved, keeps its',
        'translation, its origin, any hold on it and any waiver.',
        '',
        'A paragraph whose text HAS changed comes back untranslated. Its translation',
        'and any hold on it are gone, and nothing in the reply names it — the only',
        'sign is the count moving. Moving a paragraph does not lose the translation,',
        'but it does lose the hold, and so does a banked wording replacing a machine',
        'draft that no longer fits.',
        '',
        'Wording that no longer fits for some other reason — a change to the masking',
        'or to config/dnt.txt, with the paragraph itself untouched — is kept rather',
        'than deleted, and reported as failing.',
        '',
        `The register this document is frozen in (${doc.tone || 'the one it was extracted in'})`,
        'is not touched. Starting over in another register is a different control,',
        'and it discards everything.',
        '',
        'Only wording already banked with Commit to memory can be recovered.',
      ].join('\n'),
      'Re-extract',
    )
    if (!ok) return
    say(`— re-extract ${doc.source} [${doc.lang}] —`, 'plain', true)
    await extract()
  }

  return (
    <>
      <div className="bar">
        <ModelPicker />
        <span className="sep" />

        {/*
          One bound for all three run buttons, because `limit` bounds every
          selection mode. A control wired to Translate pending alone would lie
          about the two beside it.

          **"At most", never "Next".** The bound takes the front of the selection
          and does not advance: Translate pending drains its queue so repeated
          runs do walk the document, but a polished segment is still translated
          prose and is selected again — three bounded Polish runs ask for the
          same three segments and bill for each. A "Next 25" label is a promise
          two of the three buttons cannot keep.
        */}
        <select
          title="Most segments this run sends to the model. Taken from the front of the selection, so this bounds spend rather than walking through the document."
          value={limit}
          disabled={running}
          onChange={e => { setLimit(Number(e.target.value)) }}
        >
          {BOUNDS.map(n => (
            <option key={n} value={n}>{n === 0 ? 'All of it' : `At most ${n}`}</option>
          ))}
        </select>

        <button type="button" disabled={!ready} onClick={() => void runJob('draft')}>Translate pending</button>
        <button type="button" disabled={!ready} onClick={() => void runJob('polish')}>Polish prose</button>
        <button type="button" disabled={!ready} onClick={() => void runJob('repair')}>Repair failing</button>

        <span className="sep" />

        <button
          type="button"
          disabled={!ready}
          title="Read the source file again and re-parse it. A paragraph whose text has not changed keeps its translation; one that changed comes back untranslated."
          onClick={() => void reExtract()}
        >
          Re-extract
        </button>
        <button type="button" disabled={!ready} onClick={() => void check()}>Check</button>
        <button
          type="button"
          disabled={!settled()}
          title="Read the chapter as it renders, continuously"
          onClick={async () => { await save(); routes.go(routes.read(doc.source, doc.lang)) }}
        >
          Read
        </button>
        <button type="button" disabled={!ready} onClick={() => void render()}>Write file</button>
        <button type="button" className="key" disabled={!ready} onClick={() => void commit()}>Commit to memory</button>

        <span className="spacer" />

        <select
          value={filter}
          onChange={e => { setFilter(e.target.value as Filter) }}
          title="Which segments the ledger lists"
        >
          <option value="all">All segments</option>
          <option value="pending">Untranslated</option>
          <option value="failing">Failing</option>
          <option value="held">Held</option>
          <option value="waived">Waived</option>
        </select>

        <span className="tally">
          <span><b>{report.translated}</b>/{report.segments} translated</span>
          {report.errors > 0 && (
            <span className="err"><b>{report.errors}</b> error{report.errors === 1 ? '' : 's'}</span>
          )}
          {report.warnings > 0 && (
            <span className="warn"><b>{report.warnings}</b> warning{report.warnings === 1 ? '' : 's'}</span>
          )}
          {report.errors === 0 && report.warnings === 0 && report.translated === report.segments && (
            <span className="ok">clean</span>
          )}
          {dirty >= 0 && drafts.size() > 0 && (
            <span title="Unsaved edits. They are written when the field loses focus, or with Ctrl+Enter.">
              <b>{drafts.size()}</b> unsaved
            </span>
          )}
        </span>
      </div>

      <div className="bar" style={{ paddingTop: 7, paddingBottom: 7 }}>
        <span className="note-line" style={{ maxWidth: 'none' }}>
          {doc.source} · {doc.lang} · register <b style={{ color: 'var(--vellum)' }}>{doc.tone || 'unknown'}</b>
        </span>
        <button type="button" className="quiet" disabled={!ready} onClick={() => { setStartingOver(true) }}>
          Start over in another register…
        </button>
        <span className="spacer" />
      </div>

      {startingOver && <StartOver onClose={() => { setStartingOver(false) }} onChoose={startOver} />}
    </>
  )
}

/**
 * The one control that discards a document.
 *
 * **The register is typed, not picked from a list, and it is not pre-filled.**
 * The contract *withdrew* the instruction to forward `GET /api/doc`'s own
 * `tone`: nothing validates a register value, so a client that guesses is not
 * refused — it is handed the wrong one, silently, and the next commit banks the
 * whole book under it. A person has to choose. The document's current register
 * is shown beside the field because that is what somebody choosing needs to
 * know, and it is deliberately not the field's value.
 *
 * A list would be the friendlier control and is the wrong one for a second
 * reason: the registers this build knows live in `translate._LANG_BRIEFS` and
 * are not on the wire, so a list here is a copy of server knowledge that goes
 * stale the day one is added.
 */
function StartOver({ onClose, onChoose }: {
  onClose: () => void
  onChoose: (register: string) => Promise<void>
}) {
  const doc = useStore(s => s.doc)
  const say = useStore(s => s.say)
  const [register, setRegister] = useState('')

  if (!doc) return null

  const go = async () => {
    const chosen = register.trim()
    if (!chosen) return
    const ok = await ask(
      `Start ${doc.source} over in “${chosen}”?`,
      [
        'Everything this document holds is discarded and the source is parsed again',
        'from nothing. No translation, no hold, no waiver and no origin survives.',
        '',
        `${doc.report.translated} of ${doc.report.segments} segments are translated now.`,
        '',
        'The register is part of the translation memory key, so wording banked under',
        `the old register (${doc.tone || 'unknown'}) will not come back under the new one.`,
        'Wording banked under the new register will.',
        '',
        'Nothing validates a register name. An unrecognized one is accepted and',
        'silently selects the default brief — so a typo here is not refused, it is',
        'obeyed.',
      ].join('\n'),
      'Discard and re-extract',
      true,
    )
    if (!ok) return
    onClose()
    say(`— start over · ${doc.source} [${doc.lang}] · register ${chosen} —`, 'plain', true)
    await onChoose(chosen)
  }

  return (
    <div className="bar" style={{ background: 'var(--panel-2)' }}>
      <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <span style={{ color: 'var(--muted)', fontSize: 12 }}>New register</span>
        <input
          autoFocus
          spellCheck={false}
          autoComplete="off"
          placeholder="type a register"
          value={register}
          onChange={e => { setRegister(e.target.value) }}
          // Never while the IME is composing: Enter confirms a candidate, and
          // this control discards a document.
          onKeyDown={e => { if (!e.nativeEvent.isComposing && e.key === 'Enter') void go() }}
        />
      </label>
      <span className="note-line" style={{ maxWidth: 'none' }}>
        frozen in <b style={{ color: 'var(--vellum)' }}>{doc.tone || 'unknown'}</b> now
      </span>
      <button type="button" onClick={onClose}>Cancel</button>
      <button
        type="button"
        style={{ borderColor: 'var(--rubric)', color: 'var(--rubric)' }}
        disabled={!register.trim()}
        onClick={() => void go()}
      >
        Start over…
      </button>
    </div>
  )
}

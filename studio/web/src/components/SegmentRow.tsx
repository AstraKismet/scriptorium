/**
 * One segment: what the source says, what this project says back, and every
 * fact about the wording that a reviewer would otherwise have to guess.
 *
 * **The target field is an uncontrolled `<textarea>`.** `defaultValue` on mount,
 * the value read from the DOM, and nothing ever written back from state while a
 * person could be typing. That is not a style preference — React's still-open
 * IME defect is `#3926`, its destructive half is React writing `node.value` back
 * mid-composition inside `if (value != null)`, and an uncontrolled textarea makes
 * that branch unreachable. A controlled input on any editable field here
 * re-opens it, and the language this project exists to write is composed.
 *
 * The one write this file makes to `node.value` is in an effect, and it is
 * guarded three ways — see below.
 */
import { memo, useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'

import { ask } from './Confirm'
import { Marked, sameSlots } from '../marks'
import * as drafts from '../drafts'
import { isError, type Segment } from '../contract'
import { useStore } from '../store'

const grow = (el: HTMLTextAreaElement): void => {
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

export const SegmentRow = memo(function SegmentRow({ seg }: { seg: Segment }) {
  const running = useStore(s => s.running)
  const focused = useStore(s => s.focused)
  const setFocused = useStore(s => s.setFocused)
  const save = useStore(s => s.save)
  const runJob = useStore(s => s.runJob)
  const setHold = useStore(s => s.setHold)
  const setWaive = useStore(s => s.setWaive)

  const box = useRef<HTMLTextAreaElement>(null)
  const [slotsDiffer, setSlotsDiffer] = useState(false)

  // **Read from the registry, never held beside it.** A row that kept its own
  // `dirty` flag was told when an edit began and never when it ended: `save()`
  // forgets the entry, and the dot stayed on wording that had been written
  // minutes ago. The registry is the one place that knows, so the row asks it —
  // and it only notifies on a crossing, so this costs nothing per keystroke.
  const dirty = useSyncExternalStore(
    drafts.subscribe,
    useCallback(() => drafts.has(seg.id), [seg.id]),
  )

  /**
   * Adopt a stored target that moved underneath this row.
   *
   * A run, another window or `lx` in a terminal can change the wording while
   * this row is on screen, and an uncontrolled field would keep showing the old
   * text forever. Three guards, and each one closes a different way this could
   * destroy work:
   *
   *   1. a row holding an unsaved edit keeps it — a reviewer's own words are
   *      never replaced by anything, which is the rule the whole surface follows;
   *   2. a field that has focus is never written into — that is the composition
   *      case, and `isComposing` would not be enough because a caret in an
   *      un-composed field is still a caret somebody is using;
   *   3. and the write only happens when the text actually differs, so a
   *      re-render that changed nothing cannot move a caret.
   */
  useEffect(() => {
    const el = box.current
    if (!el) return
    if (drafts.has(seg.id)) return
    if (document.activeElement === el) return
    if (el.value === seg.target) return
    el.value = seg.target
    grow(el)
    setSlotsDiffer(false)
  }, [seg.id, seg.target, seg.token])

  useEffect(() => { if (box.current) grow(box.current) }, [])

  const bad = seg.issues.some(isError)
  const isFocused = focused === seg.id
  const held = seg.review === 'held'

  const touched = () => {
    const el = box.current
    if (!el) return
    drafts.set(seg.id, el.value, seg.target)
    // Advisory only, and it never gates anything: what a target may contain is
    // `translate.accept`'s question and `checks.py`'s, both on the server. This
    // is the placeholder warning arriving while there is still time to fix it,
    // and the authoritative verdict comes with the next check.
    setSlotsDiffer(!sameSlots(el.value, seg.source))
  }

  /**
   * Send this one segment to the model.
   *
   * Naming an id reaches a segment the queue would not — `ids` outranks the
   * mode's own table, the hold exclusion and the pre-filter that drops segments
   * a person wrote. The write is where that stops: `store.save_targets` refuses
   * an `llm:*` write over `human` wording, **after** the model has been called
   * and billed. Measured against a live backend, 765 tokens for one paragraph
   * and `applied 0`. So this asks before spending rather than reporting
   * afterwards.
   *
   * **Flush first, then read the origin — in that order.** An edit sits in
   * `drafts` and does not touch the stored segment, so a paragraph the reviewer
   * has just retyped still reports the machine origin it had before; deciding
   * from that skipped the question, and then the run's own save wrote
   * `origin: "human"` a moment later and the run was refused at the write.
   */
  const again = async (mode: 'draft' | 'polish') => {
    if (running) return
    await save()
    const now = useStore.getState().doc?.segments.find(s => s.id === seg.id)
    if (!now || useStore.getState().running) return
    // **A hold does not stop this control, and that is the one place the two
    // sit beside each other.** `ids` outranks the hold exclusion — the contract
    // says so in as many words, because naming an id is a person pointing rather
    // than a queue sweeping — so the button two rows below "Hold" is precisely
    // the thing a hold does not protect against. Origin precedence does not
    // cover it either: the write is `llm:*` over whatever is there, and `review`
    // is not `origin`.
    if (now.review === 'held') {
      const anyway = await ask(
        'Send a held segment to the model?',
        [
          `${seg.id} is held, which keeps every queue that selects work off it.`,
          '',
          'Naming a segment is not a queue, so this run reaches it anyway — that is',
          'deliberate, and it is why the hold does not refuse it here. The hold itself',
          'survives: the write touches the target, the status and the origin, and never',
          'the review field.',
          '',
          'What is replaced is the wording you were keeping.',
        ].join('\n'),
        'Send it anyway',
        true,
      )
      if (!anyway) return
    }
    let over = false
    if (now.origin === 'human') {
      over = await ask(
        'Replace wording a person wrote?',
        [
          `${seg.id} was last written by a person, not by a model.`,
          '',
          'A model run does not replace that wording unless it is told to. Without',
          'confirming, this run would still call the model and still cost tokens, and',
          'then leave the segment exactly as it is.',
          '',
          'Confirming replaces it. What is there now is kept only if it has already',
          'been banked with Commit to memory.',
        ].join('\n'),
        'Replace it',
        true,
      )
      if (!over) return
    }
    // A hold means "no queue may take this"; naming the id is the reviewer
    // overriding that deliberately, and the hold itself survives — the write
    // touches `target`, `status` and `origin` and never `review`.
    await runJob(mode, [seg.id], over, now.review === 'held' ? 'held, and named anyway' : '')
  }

  return (
    <div
      className={[
        'row',
        bad ? 'failing' : '',
        held ? 'held' : '',
        dirty ? 'dirty' : '',
      ].filter(Boolean).join(' ')}
      aria-selected={isFocused}
      onClick={() => { setFocused(seg.id) }}
    >
      <div className="gutter">
        {seg.id}
        <span className="kind">{seg.kind}</span>
        {seg.target && (
          <span className={bad ? 'mark bad' : 'mark ok'} title={bad ? 'fails a check' : 'passes every check'}>
            {bad ? '✗' : '✓'}
          </span>
        )}
        {(held || seg.waived) && (
          <span className="flags">
            {held && <span className="held" title="held: no queue that selects work takes this">held</span>}
            {held && seg.waived && ' · '}
            {seg.waived && <span className="waived" title="waived: the rules judgement can overrule report at warn here">waived</span>}
          </span>
        )}
        {seg.origin && <span className="origin" title="where this wording came from">{seg.origin}</span>}
      </div>

      <div className="src"><Marked text={seg.source} /></div>

      <div className="tgt">
        <textarea
          ref={box}
          rows={1}
          spellCheck={false}
          placeholder="untranslated"
          defaultValue={drafts.get(seg.id) ?? seg.target}
          onInput={() => { const el = box.current; if (el) { grow(el); touched() } }}
          onFocus={() => { setFocused(seg.id) }}
          onBlur={() => { void save() }}
          onKeyDown={e => {
            // **Never while the IME is composing.** Enter is the candidate-
            // confirmation key in every Chinese input method, and this is a
            // workbench for writing Chinese: a shortcut that fires mid-composition
            // banks half a sentence and moves the caret away from it. React's own
            // defect is about writing `node.value` back; this is the other half,
            // and no framework closes it for you.
            if (e.nativeEvent.isComposing) return
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
              e.preventDefault()
              void save()
              const rows = [...document.querySelectorAll<HTMLTextAreaElement>('.ledger textarea')]
              const next = rows[rows.indexOf(e.currentTarget) + 1]
              if (next) { next.focus(); next.scrollIntoView({ block: 'center' }) }
            }
          }}
        />
      </div>

      {isFocused && (
        <div className="acts">
          <button type="button" disabled={running} onClick={() => void again('draft')}
            title="Translate this segment again from scratch, through the draft stage">
            Draft again
          </button>
          {seg.target && (
            <button type="button" disabled={running} onClick={() => void again('polish')}
              title="Hand the model the wording that is here as a draft to improve">
              Polish
            </button>
          )}
          <button
            type="button"
            disabled={running || (!held && !seg.target)}
            title={
              held
                ? 'Return this segment to the queues that select work'
                : seg.target
                  ? 'Keep every queue off this segment. It stays editable, and a named id still reaches it.'
                  : 'Holding needs a wording to hold — the queue is the only thing that would write one'
            }
            onClick={() => void setHold([seg.id], !held)}
          >
            {held ? 'Unhold' : 'Hold'}
          </button>
          <button
            type="button"
            disabled={running || (!seg.waived && !bad)}
            title={
              seg.waived
                ? 'Take back the waiver: these findings fail the build again'
                : bad
                  ? 'Stand by this wording: the rules a reviewer can overrule report at warn instead of failing'
                  : 'A waiver answers a finding, and this segment has none'
            }
            onClick={() => void setWaive([seg.id], !seg.waived)}
          >
            {seg.waived ? 'Unwaive' : 'Waive'}
          </button>
          {slotsDiffer && (
            <span className="why" style={{ color: 'var(--amber)' }}>
              the placeholders here differ from the source — the check will say so
            </span>
          )}
        </div>
      )}

      {seg.issues.length > 0 && (
        <div className="notes">
          {seg.issues.map((issue, i) => (
            <div key={`${issue.rule}-${i}`} className={isError(issue) ? 'note' : 'note warn'}>
              <b>{issue.rule}</b>{issue.message}
            </div>
          ))}
        </div>
      )}
    </div>
  )
})

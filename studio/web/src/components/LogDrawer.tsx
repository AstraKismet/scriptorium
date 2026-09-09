/**
 * What happened, in the order it happened.
 *
 * A run leaves **no record on disk** — job state is in-process and dies with the
 * server — so this drawer is the only account of what a run did, which is why it
 * keeps four thousand lines rather than a screenful.
 *
 * **The cost is shown as five integers and never as a sentence.** The run
 * already said what it cost, once, through `translate.usage_line` on the same
 * `progress` sink `lx` prints from, and that line is in the log below. Wording
 * it a second time here is the drift invariant 8 exists to stop — one fact, two
 * surfaces, and they come apart the day either is edited. The structured field
 * exists because the contract forbids parsing the log, so what it is used for is
 * the thing a sentence cannot be: a table you can read a number out of.
 */
import { useEffect, useRef } from 'react'

import { useStore } from '../store'

export function LogDrawer() {
  const log = useStore(s => s.log)
  const cost = useStore(s => s.runCost)
  const clear = useStore(s => s.clearLog)
  const running = useStore(s => s.running)
  const abandon = useStore(s => s.abandon)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = box.current
    if (el) el.scrollTop = el.scrollHeight
  }, [log])

  if (!log.length) return null

  return (
    <>
      <div className="log-head">
        <h3>Log</h3>
        {cost && (
          <div className="cost" title="What the backend said this run cost. `total` is prompt + completion, computed by the server; it is a floor rather than a cost unless every reply reported one.">
            <span><b>{cost.usage.prompt.toLocaleString()}</b> in</span>
            <span><b>{cost.usage.completion.toLocaleString()}</b> out</span>
            <span><b>{cost.usage.total.toLocaleString()}</b> total</span>
            <span><b>{cost.usage.reported}</b>/{cost.usage.replies} replies reported</span>
          </div>
        )}
        {running && (
          // Not a Stop button, and the label says so. There is no cancel
          // endpoint on this surface and there is no way to add one from here,
          // so the honest act is to stop *following*: the server thread carries
          // on writing either way. Without this control a backend that blocks
          // rather than answering — which is what a llama.cpp router does while
          // it loads a model — leaves every run control disabled until `lx web`
          // is restarted.
          <button
            type="button"
            className="quiet"
            onClick={abandon}
            title="The run is not cancelled — nothing can cancel one. This only stops watching it."
          >
            Stop following
          </button>
        )}
        <button type="button" className="quiet" onClick={clear}>Clear</button>
      </div>
      <div className="log" ref={box}>
        {log.map(l => (
          <div
            key={l.n}
            className={[l.level === 'plain' ? '' : l.level, l.head ? 'head' : ''].filter(Boolean).join(' ')}
          >
            {l.text}
          </div>
        ))}
      </div>
    </>
  )
}

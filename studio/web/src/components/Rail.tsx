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
 */
import { useStore } from '../store'
import * as routes from '../router'

export function Rail() {
  const state = useStore(s => s.state)
  const at = useStore(s => s.at)
  const say = useStore(s => s.say)
  const extract = useStore(s => s.extract)
  const open = useStore(s => s.open)

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
        {state.docs.length === 0 && <p className="none">None yet.</p>}
        {state.docs.map(d => (
          <button
            key={d.source + ' ' + d.lang}
            type="button"
            className="doc"
            aria-current={current(d.source, d.lang)}
            onClick={() => { routes.go(routes.doc(d.source, d.lang)) }}
          >
            <b>{d.source}</b>
            <small>{d.lang} · {d.done}/{d.total}</small>
            <span className="meter">
              <i style={{ width: `${Math.round((100 * d.done) / Math.max(d.total, 1))}%` }} />
            </span>
          </button>
        ))}

        <h2>Not yet extracted</h2>
        {state.untracked.length === 0 && (
          <p className="none">Nothing new matches <code>sources</code>.</p>
        )}
        {state.untracked.map(c => (
          <button
            key={c.source + ' ' + c.lang}
            type="button"
            className="doc"
            onClick={async () => {
              say(`— extract ${c.source} [${c.lang}] —`, 'plain', true)
              // `open` first so the extract is addressed to a document this
              // page is holding, and so a failure leaves the person looking at
              // the thing they clicked rather than at nothing. A first extract
              // produces none of `kept` / `replaced` / `ambiguous`, so the noise
              // those lines would make is not owed here — but they are reported
              // by the same action either way, because a rule with two spellings
              // is a rule that comes apart.
              await open(c.source, c.lang)
              await extract()
            }}
          >
            <b>{c.source}</b>
            <small>{c.lang} · extract</small>
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

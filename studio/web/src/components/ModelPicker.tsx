/**
 * Which backend and which model this run uses — and, by default, neither.
 *
 * **The head option of each control sends nothing.** Seeding either box is the
 * defect this pair was rebuilt around: `cli.do_models` resolves stage `draft`
 * only, `config.resolve_route` puts the caller's model first, and this toolbar
 * has three run buttons — so a seeded model silently overrides `routing.polish`
 * and `routing.repair`, and a seeded *provider* does the same one level up,
 * because a `provider` that differs from the stage's own is an override that
 * drops that stage's model with it. Left empty, each stage resolves its own
 * entry on the server, which is what the routing table exists for.
 *
 * The listing is **advisory and gates nothing**: a single-model `llama-server`
 * ignores the `model` field entirely and still answers, so the "type an id"
 * escape hatch is not a convenience — a control that only offered what came back
 * would refuse a working configuration.
 */
import { useState } from 'react'

import { useStore } from '../store'
import { ROUTING_STAGES } from '../contract'

/** The escape hatch's own option value.
 *
 *  It has to be a string no backend would serve as a model id, and there is no
 *  such string — an id is remote text and may be anything printable. The cost of
 *  a collision is bounded and visible rather than silent: a backend serving a
 *  model literally called `__other__` would show a text field where the reviewer
 *  expected a selection, and typing the id is what the field is for. */
const OTHER = '__other__'

export function ModelPicker() {
  const state = useStore(s => s.state)
  const provider = useStore(s => s.provider)
  const model = useStore(s => s.model)
  const models = useStore(s => s.models)
  const loading = useStore(s => s.modelsLoading)
  const chooseProvider = useStore(s => s.chooseProvider)
  const setModel = useStore(s => s.setModel)
  const running = useStore(s => s.running)

  const [typing, setTyping] = useState(false)

  if (!state) return null

  const stages = ROUTING_STAGES
    .map(k => `${k} → ${state.routing[k]?.provider || '?'}`)
    .join(', ')

  const head = models?.configured
    ? `configured: ${models.configured}`
    : 'each stage’s own model'

  const note = loading
    // Never a "retrying" message: a llama.cpp router *blocks* the caller while
    // it loads a model rather than answering 503, so a slow first request is a
    // slow request and saying otherwise invents a story about what is happening.
    ? 'asking the backend…'
    : models?.error
      ? 'listing unavailable'
      : models
        ? (models.models.length ? `${models.models.length} models` : 'no list published')
        : ''

  return (
    <>
      <select
        title="Backend for this run"
        value={provider}
        disabled={running}
        onChange={e => { setTyping(false); chooseProvider(e.target.value) }}
      >
        <option value="">{`each stage’s own backend  (${stages})`}</option>
        {state.providers.map(p => {
          const blocked = p.needs_key && !p.key_present
          return (
            <option key={p.name} value={p.name} disabled={blocked}>
              {blocked ? `${p.name} — ${p.key_env} not set` : p.name}
            </option>
          )
        })}
      </select>

      <select
        title="Model for this run. Left alone, each stage uses its own configured model."
        value={typing ? OTHER : model}
        disabled={running}
        onChange={e => {
          if (e.target.value === OTHER) { setTyping(true); setModel('') } else { setTyping(false); setModel(e.target.value) }
        }}
      >
        <option value="">{loading ? 'asking the backend…' : head}</option>
        {/* Built as options with a value and a text child — never as a string of
            markup. A model id is remote text: the boundary filter that cleans it
            is about *control* characters, and `<`, `>`, `"` and `'` are all legal
            in an id and pass it untouched. */}
        {(models?.models ?? []).map(m => (
          <option key={m.id} value={m.id}>{m.status ? `${m.id}  · ${m.status}` : m.id}</option>
        ))}
        <option value={OTHER}>Type an id…</option>
      </select>

      {typing && (
        <input
          size={26}
          spellCheck={false}
          autoComplete="off"
          placeholder="model id"
          title="An id this backend did not list"
          autoFocus
          value={model}
          disabled={running}
          onChange={e => { setModel(e.target.value.trim()) }}
        />
      )}

      <span className="note-line" title={models?.error ?? ''}>{note}</span>
    </>
  )
}

/**
 * Which backend, and which model, serves each stage.
 *
 * Three independent `routing.*` keys, three independent writes, and one screen
 * that does nothing else. It is separate from the backend editor because the two
 * have different save semantics and mixing them produced a defect: a Save that
 * changed all three stages wrote only the first, because each successful write
 * repainted the form from its own reply and the repaint reset the two controls
 * the loop had not reached yet.
 *
 * **What is rendered is the resolved projection**, never `cfg["routing"]`. The
 * configured value has two legal spellings on purpose — a provider name, or
 * `{provider, model}` — and a consumer must not have to know both. What is
 * *written* is the bare provider name, because both spellings are legal and
 * every configuration on disk uses that one; a screen that always emitted the
 * object form would migrate them all.
 */
import { useState } from 'react'

import * as api from '../api'
import { ApiError } from '../api'
import { useStore } from '../store'
import { ROUTING_STAGES, type Stage } from '../contract'

export function RoutingScreen() {
  const state = useStore(s => s.state)
  const absorb = useStore(s => s.absorb)
  const [busy, setBusy] = useState<Stage | null>(null)
  const [note, setNote] = useState<{ text: string; level: 'good' | 'bad' } | null>(null)

  if (!state) return null

  const write = async (stage: Stage, provider: string) => {
    if (busy) return
    setBusy(stage)
    setNote(null)
    try {
      const reply = await api.postConfig({ key: `routing.${stage}`, value: provider })
      absorb(reply.providers, reply.routing)
      setNote({ text: `routing.${stage} → ${provider}.`, level: 'good' })
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0
      const said = err instanceof Error ? err.message : String(err)
      setNote({
        text: (status === 403 ? 'That key cannot be written from here. ' : 'Refused. ') + said,
        level: 'bad',
      })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="screen">
      <div className="sheet">
        <h2>Routing</h2>
        <p>
          Which backend serves each stage. A run sends no backend and no model of its own
          unless the toolbar names one, so this is what actually decides where the document
          goes. What a profile <em>contains</em> is <a href="#/backends">Backends</a>.
        </p>

        <div className="form">
          {ROUTING_STAGES.map(stage => {
            const now = state.routing[stage]
            return (
              <div key={stage}>
                <label>
                  <span>{stage}</span>
                  <select
                    value={now?.provider ?? ''}
                    disabled={busy !== null}
                    onChange={e => { void write(stage, e.target.value) }}
                  >
                    {/* A stage whose configured entry is malformed resolves to an
                        empty provider, so *every* value in the list differs from
                        it. The empty head option is what keeps that state visible
                        instead of silently showing a guess as though it were the
                        answer — nothing is written until somebody chooses. */}
                    <option value="" disabled>
                      {now?.error ? 'unreadable — choose one' : 'not configured'}
                    </option>
                    {state.providers.map(p => (
                      <option key={p.name} value={p.name}>{p.name}</option>
                    ))}
                  </select>
                </label>
                <p className={now?.error ? 'hint bad' : 'hint'}>
                  {now?.error
                    ? now.error
                    : now?.model
                      ? `resolves to ${now.provider} · ${now.model}`
                      : now?.provider
                        ? `resolves to ${now.provider} · that backend’s own default model`
                        : 'this stage has no readable routing entry'}
                </p>
              </div>
            )
          })}
          {note && <p className={`hint ${note.level}`}>{note.text}</p>}
          <p className="hint">
            Each stage is written on its own, the moment it is chosen — one key per
            request, which is what the endpoint accepts and what keeps two of them from
            reading and rewriting the same file at once.
          </p>
          <p className="hint">
            Pinning a <em>model</em> to a stage is not writable from here: it needs the
            object form of a routing value, and every configuration on disk uses the bare
            provider name. Use <code>lx routing set {'<stage> <provider> --model <id>'}</code>.
          </p>
        </div>
      </div>
    </div>
  )
}

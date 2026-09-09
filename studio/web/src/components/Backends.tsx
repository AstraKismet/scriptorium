/**
 * What a backend profile contains. **Not** which backend runs which stage — that
 * is `#/routing`, and the split is a maintainer requirement with a defect behind
 * it: the two halves have different save semantics (per-field writes against a
 * named provider, versus three independent `routing.*` keys), and mixing them is
 * what produced the save-only-the-first-stage bug. Splitting them makes that
 * class unreachable rather than guarded.
 *
 * **Every rule this screen enforces is the server's.** It sends one key per
 * request and renders the refusal; `cli.writable_key` and the field table behind
 * `cli.do_config_set` decide what may be written and what a value may be. A
 * second copy of those rules in TypeScript is how two surfaces come to disagree
 * about what is writable — which is why even the base-URL acknowledgement is
 * *sent* rather than pre-checked here: the checkbox carries the person's answer
 * and the server is the only gate, on a write **and** on a removal.
 */
import { useEffect, useRef, useState } from 'react'

import * as api from '../api'
import { ApiError } from '../api'
import { useStore } from '../store'
import type { Model, Provider } from '../contract'

/**
 * The write order, and it is load-bearing.
 *
 * Ordered so that **any prefix of it is a coherent configuration**, which is
 * what a refusal in the middle leaves behind. `base_url` leads because it has
 * the most ways to be refused — no scheme, no host, userinfo, a query string,
 * and the acknowledgement — so a rejection at step one creates nothing at all.
 * Writing `kind` first was the obvious order and is the worst one: `config.set_in`
 * opens the provider block on the *first* key written, so a `base_url` refused
 * straight after leaves a backend that looks configured in every list, silently
 * resolves to a hardcoded `http://localhost:11434/v1`, and **cannot be deleted
 * over HTTP**.
 */
const FIELDS = [
  'base_url', 'api_key_env', 'kind', 'model',
  'timeout', 'temperature', 'max_tokens', 'retries',
] as const
type Field = (typeof FIELDS)[number]

const NUMERIC: ReadonlySet<Field> = new Set(['timeout', 'temperature', 'max_tokens', 'retries'])

/** What the projection holds for a field today, as the string a box shows.
 *  A `null` number is **absent from the spec, so the transport default applies**
 *  — the box stays blank, because writing 120 into it lets Save pin an inherited
 *  value. The defaults live in `Provider.__init__` and this page holds no copy. */
function stored(p: Provider | null, field: Field): string {
  if (!p) return ''
  if (field === 'api_key_env') return p.key_env
  const value = p[field]
  return value == null ? '' : String(value)
}

export function Backends() {
  const state = useStore(s => s.state)
  const absorb = useStore(s => s.absorb)
  const [chosen, setChosen] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  if (!state) return null

  return (
    <div className="screen">
      <div className="sheet">
        <h2>Backends</h2>
        <p>
          What each profile contains. Which backend serves each stage is <a href="#/routing">Routing</a>,
          and they are separate on purpose: these are per-field writes against one named
          provider, those are three independent keys.
        </p>

        <div className="form">
          {state.providers.map(p => (
            <button
              key={p.name}
              type="button"
              className="doc"
              style={{ padding: '10px 12px', border: '1px solid var(--rule)', borderRadius: 3 }}
              aria-current={chosen === p.name}
              onClick={() => { setCreating(false); setChosen(chosen === p.name ? null : p.name) }}
            >
              <b>{p.name}</b>
              <small>
                {p.kind} · {p.base_url || 'no base_url'}
                {p.model ? ` · ${p.model}` : ''}
                {p.needs_key ? (p.key_present ? ` · ${p.key_env} set` : ` · ${p.key_env} NOT set`) : ' · no key needed'}
              </small>
              {p.error && <small style={{ color: 'var(--rubric)' }}>{p.error}</small>}
            </button>
          ))}
          <footer>
            <button type="button" onClick={() => { setChosen(null); setCreating(true) }}>
              New backend…
            </button>
            <span className="spacer" style={{ flex: 1 }} />
          </footer>
          {/* Said rather than offered: removing a block is not on the endpoint's
              allowlist, so a delete control here would be a control that 403s
              every time it is pressed. */}
          <p className="hint">
            A backend cannot be removed from here — block writes are refused at any
            depth. Use <code>lx config unset providers.&lt;name&gt;</code>, or edit{' '}
            <code>lx.config.json</code>.
          </p>
        </div>

        {(chosen || creating) && (
          <Profile
            key={chosen ?? '__new__'}
            provider={state.providers.find(p => p.name === chosen) ?? null}
            onWrote={(providers, routing, name) => {
              absorb(providers, routing)
              setCreating(false)
              setChosen(name)
            }}
          />
        )}
      </div>
    </div>
  )
}

function Profile({ provider, onWrote }: {
  provider: Provider | null
  onWrote: (providers: Provider[], routing: Record<string, { provider: string; model: string; error?: string }>, name: string) => void
}) {
  const form = useRef<HTMLFormElement>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ text: string; level: 'good' | 'bad' | 'warn' } | null>(null)
  const [models, setModels] = useState<Model[] | null>(null)

  // The editor's model box is fed by the same listing the toolbar uses, so a
  // 47-character id is picked rather than copied by hand between two controls.
  // Its own sequence token: a listing against a black-holed backend can take
  // most of a minute, and a slow reply arriving after the form was refilled used
  // to write *another* backend's model id into this one.
  useEffect(() => {
    if (!provider) { setModels(null); return }
    let live = true
    setModels(null)
    api.getModels(provider.name)
      .then(r => { if (live) setModels(r.models) })
      .catch(() => { if (live) setModels([]) })
    return () => { live = false }
  }, [provider])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy) return
    const el = form.current
    if (!el) return

    // **Read every control into a snapshot before the first await.** Each write
    // repaints from its own reply, and a repaint rebuilds the controls — so a
    // loop that read them lazily saved only the first changed field. It is the
    // third instance of one shape on this surface, so the rule is "a control read
    // after an await is a control somebody else may have rewritten", not a patch
    // per site.
    const data = new FormData(el)
    const name = String(data.get('name') ?? '').trim()
    const want = new Map<Field, string>()
    for (const field of FIELDS) want.set(field, String(data.get(field) ?? '').trim())
    const acknowledged = data.get('ack') === 'on'

    if (!name) {
      setNote({ text: 'Name the backend first.', level: 'bad' })
      return
    }

    // On a *new* backend `base_url` leads, so a refusal creates nothing at all.
    // On an existing one where both credential fields change, `api_key_env`
    // leads: the acknowledgement says the key named below goes with the new URL,
    // and writing the URL first and then having the key refused leaves the
    // backend pointing at the new host with the *old* credential — the one
    // sentence of that acknowledgement no ordering of two requests can keep
    // unless the credential half lands first.
    const order: Field[] = provider
      ? [...FIELDS].sort((a, b) => (a === 'api_key_env' ? -1 : b === 'api_key_env' ? 1 : 0))
      : [...FIELDS]

    setBusy(true)
    const wrote: string[] = []
    try {
      for (const field of order) {
        const value = want.get(field) ?? ''
        const had = stored(provider, field)
        // Compared as numbers where the field is one: a box reports a string and
        // the projection reports a number, so "300" against 300.0 is a spurious
        // write of a value that did not change — and every write here is a
        // request.
        const same = NUMERIC.has(field) && value !== '' && had !== ''
          ? Number(value) === Number(had)
          : value === had
        if (same) continue
        if (!value && !provider) continue // blank on a new backend: nothing to write

        // **An emptied box removes the key; it does not write a falsy value.** A
        // cleared timeout meant `Number('')` — zero, a legal number and a useless
        // one — and a cleared base_url meant the empty string rather than the
        // provider's own default coming back. `api_key_env` is the one exception:
        // an empty value there is the documented way to say this backend needs no
        // credential, so it is written rather than removed.
        const body: Parameters<typeof api.postConfig>[0] = { key: `providers.${name}.${field}` }
        if (!value && field !== 'api_key_env') body.unset = true
        else body.value = NUMERIC.has(field) ? Number(value) : value
        // Sent whatever its state, never pre-checked. Removal needs it too:
        // dropping a `base_url` redirects the credential just as writing one does.
        if (field === 'base_url') body.confirm_base_url = acknowledged

        const reply = await api.postConfig(body)
        wrote.push(field)
        onWrote(reply.providers, reply.routing, name)
      }
      setNote(
        wrote.length
          ? { text: `Saved: ${wrote.join(', ')}.`, level: 'good' }
          : { text: 'Nothing had changed.', level: 'warn' },
      )
    } catch (err) {
      // Stop rather than carry on: the fields after this one would land on a
      // backend whose earlier field was refused. Say what did get written, or the
      // person cannot tell how far it got. **The sentence is the server's**, is
      // rendered as text, is never parsed, and the value is never echoed back
      // into the field it came from.
      const status = err instanceof ApiError ? err.status : 0
      const said = err instanceof Error ? err.message : String(err)
      setNote({
        text:
          (status === 403
            ? 'That key cannot be written from here. '
            : 'The write was refused. ') + said +
          (wrote.length ? ` — ${wrote.join(', ')} had already been saved.` : ' Nothing was saved.'),
        level: 'bad',
      })
    } finally {
      setBusy(false)
    }
  }

  const own = provider?.model ?? ''

  return (
    <form className="form" ref={form} onSubmit={e => void submit(e)}>
      <h3>{provider ? provider.name : 'New backend'}</h3>

      {provider
        ? <input type="hidden" name="name" value={provider.name} />
        : (
          <label>
            <span>Name</span>
            <input name="name" spellCheck={false} autoComplete="off" placeholder="llamacpp" autoFocus />
          </label>
        )}

      <label>
        <span>Base URL</span>
        <input name="base_url" spellCheck={false} autoComplete="off"
          defaultValue={stored(provider, 'base_url')}
          placeholder="http://127.0.0.1:8080/v1" />
      </label>
      <div className="ack">
        <input type="checkbox" name="ack" id="ack" />
        <label htmlFor="ack" style={{ display: 'block' }}>
          Changing or removing the base URL changes where the document under translation
          is sent, and the key named below goes with it. Tick to confirm — the server
          refuses the change without it.
        </label>
      </div>

      <label>
        <span>API key variable</span>
        <input name="api_key_env" spellCheck={false} autoComplete="off"
          defaultValue={stored(provider, 'api_key_env')} placeholder="OPENAI_API_KEY" />
      </label>
      <p className={provider?.needs_key && !provider.key_present ? 'hint bad' : 'hint'}>
        {!provider || !provider.needs_key
          ? 'Empty means this backend needs no key. The name of a variable is stored; a value shaped like a key is refused.'
          : provider.key_present
            ? `${provider.key_env} is set in this workbench’s environment.`
            // `key_present` is read from **this server process's** environment,
            // fixed when `lx web` started. "(no key)" would read as "you have not
            // configured this" when the real next step is a restart.
            : `${provider.key_env} is not set in the environment this workbench was started in. Export it and restart lx web — a running process cannot re-read it.`}
      </p>

      <label>
        <span>Kind</span>
        <select name="kind" defaultValue={stored(provider, 'kind') || 'openai'}>
          <option value="openai">openai</option>
          <option value="openai-compatible">openai-compatible</option>
          <option value="anthropic">anthropic</option>
        </select>
      </label>

      <label>
        <span>Model</span>
        {/* A text field rather than a select, because a *stored* id must survive a
            listing that has not answered yet: replacing a select's options resets
            its value, and a blank placeholder made a Save read the value as empty
            — which this form spells `unset` — so pressing Save while the listing
            was in flight deleted the model of a backend nobody meant to touch. */}
        <input name="model" spellCheck={false} autoComplete="off"
          defaultValue={own} list={`models-${provider?.name ?? 'new'}`}
          placeholder="the backend’s default" />
      </label>
      <datalist id={`models-${provider?.name ?? 'new'}`}>
        {(models ?? []).map(m => <option key={m.id} value={m.id}>{m.status || undefined}</option>)}
      </datalist>
      <p className="hint">
        {models === null
          ? 'asking the backend which models it serves…'
          : models.length
            ? `${models.length} listed. The listing is advisory — a single-model server ignores this field entirely and still answers, so an id it did not list is legal.`
            : 'This backend published no list. Type an id, or leave it blank for its own default.'}
      </p>

      <label><span>Timeout (s)</span><input name="timeout" type="number" min="1" step="1" defaultValue={stored(provider, 'timeout')} /></label>
      <label><span>Temperature</span><input name="temperature" type="number" min="0" max="2" step="0.05" defaultValue={stored(provider, 'temperature')} /></label>
      <label><span>Max tokens</span><input name="max_tokens" type="number" min="1" step="1" defaultValue={stored(provider, 'max_tokens')} /></label>
      <label><span>Retries</span><input name="retries" type="number" min="0" step="1" defaultValue={stored(provider, 'retries')} /></label>
      <p className="hint">
        A blank number is inherited, not zero — the transport applies its own default
        and this page does not hold a copy of it. Clearing a box removes the key.
      </p>

      {provider?.error && <p className="hint bad">{provider.error}</p>}

      <footer>
        <button type="submit" className="key" disabled={busy}>
          {busy ? 'Saving…' : 'Save changes'}
        </button>
        <span className="spacer" style={{ flex: 1 }} />
      </footer>
      {note && <p className={`hint ${note.level}`}>{note.text}</p>}
    </form>
  )
}

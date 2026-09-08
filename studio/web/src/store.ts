/**
 * The application store. **Cold state only.**
 *
 * The two-tier rule is a red line: the text a reviewer is typing, the IME
 * composition flag and a textarea's height never appear here. `drafts.ts` holds
 * the first, the row component holds the other two, and the store learns about
 * an edit exactly once — when the row crosses into dirty.
 *
 * Everything below is application logic: which document is open, which run is in
 * flight, what the log says. **No pipeline logic lives here** — not the sentence
 * rule, not the style-block matcher, not routing resolution, not which segments
 * a mode selects. Those are the server's, and a copy of one here is the drift
 * invariant 8 exists to stop.
 */
import { create } from 'zustand'

import * as api from './api'
import { ApiError } from './api'
import * as drafts from './drafts'
import {
  CONTRACT_VERSION, isError, isJobRecord,
  type DocAddress, type DocResponse, type ModelsResponse, type Mode,
  type Provider, type Routing, type Segment, type StateResponse, type Usage,
} from './contract'

export type Filter = 'all' | 'pending' | 'failing' | 'held' | 'waived'

/** How a log line reads. Deliberately not called a tone: in this project a
 *  tone is a document's register, and one word for two things on a surface that
 *  shows both is how a reader comes to misread one of them. */
export type Level = 'plain' | 'good' | 'bad' | 'warn'

export interface LogLine {
  n: number
  text: string
  level: Level
  /** A run header. Drawn as a rule across the drawer. */
  head?: boolean
}

/** What a `POST /api/job` answer said the run cost. Held so the drawer can show
 *  the five integers as a table. **Never formatted into a sentence here** — the
 *  run already says what it cost, once, in its own `log`, and two surfaces
 *  wording one fact is exactly the drift this project keeps out. */
export interface RunCost {
  usage: Usage
  at: string
  lang: string
}

export type Boot = 'loading' | 'ready' | 'incompatible' | 'failed'

interface Store {
  boot: Boot
  bootError: string
  /** The `contract_version` the server reported, when we refuse it. */
  serverContract: number | null
  state: StateResponse | null

  /** The document the person asked for. Assigned synchronously. */
  at: DocAddress | null
  /** The document on screen. Lags `at` across the fetch. */
  doc: DocResponse | null
  docLoading: boolean
  docError: string

  /** One run at a time, and every entry point reads it. */
  running: boolean
  runCost: RunCost | null

  log: LogLine[]
  filter: Filter
  /** The segment the margin is about. */
  focused: string | null

  /** `''` means each stage's own backend. */
  provider: string
  /** `''` means each stage's own model. Never seeded from `configured`. */
  model: string
  /** Which backend the model list on screen came from. */
  listedProvider: string
  models: ModelsResponse | null
  modelsLoading: boolean
  /** `0` means the whole selection. */
  limit: number

  say: (text: string, level?: Level, head?: boolean) => void
  clearLog: () => void
  setFilter: (f: Filter) => void
  setFocused: (id: string | null) => void
  setLimit: (n: number) => void
  setModel: (m: string) => void
  chooseProvider: (name: string) => void

  bootstrap: () => Promise<void>
  reloadState: () => Promise<void>
  /** Take the two projections a `POST /api/config` reply carries back.
   *
   *  A settings screen repaints from its own write's answer and **never** calls
   *  `GET /api/state` to repaint a form: that endpoint loads every segment of
   *  every document to answer, which is a strange price for redrawing one
   *  dropdown. */
  absorb: (providers: Provider[], routing: Routing) => void
  loadModels: () => Promise<void>
  open: (src: string, lang: string) => Promise<void>
  refresh: () => Promise<void>
  /** True when the ledger is showing the document `at` names. */
  settled: () => boolean
  /** The address of what is on screen — read from `doc`, never from `at`, so
   *  the two cannot disagree about which document an act is addressed to. */
  shown: () => DocAddress | null

  save: () => Promise<boolean>
  runJob: (mode: Mode, ids?: string[], overwriteHuman?: boolean, note?: string) => Promise<void>
  setHold: (ids: string[], held: boolean) => Promise<void>
  setWaive: (ids: string[], waived: boolean) => Promise<void>
  check: () => Promise<void>
  /** Re-read the source and re-parse, keeping the frozen register. */
  extract: () => Promise<void>
  /**
   * Discard everything and re-extract in a register a person chose.
   *
   * A separate action rather than a flag on `extract`, so that the two spellings
   * the server type-checks with nothing — `reset` and `tone` — are written in
   * exactly one place in this whole source tree. A test asserts that by reading
   * the files: any second call site is a second chance to send the *string*
   * `"false"`, which is truthy in Python and discards a book.
   */
  startOver: (register: string) => Promise<void>
  render: () => Promise<void>
  commit: () => Promise<void>
}

/** The log is capped so a session left open for a week does not grow without
 *  bound. It is generous on purpose: a run's log is the only record it leaves —
 *  nothing is written to disk (HANDOFF-040) — so trimming it cheaply would throw
 *  away the only account of what a run did. */
const LOG_CAP = 4000

let line = 0
let modelSeq = 0

const same = (a: DocAddress | null, b: DocAddress | null): boolean =>
  !!a && !!b && a.src === b.src && a.lang === b.lang

const reason = (e: unknown): string =>
  e instanceof Error ? e.message : String(e)

/**
 * The one place in this source tree that names `reset` or `tone` on a request.
 *
 * A plain re-extract keeps the register the document is frozen in — the server
 * falls back to the stored value — and sends **neither key**. A start-over sends
 * both, together, with `reset` as the JSON boolean literal and `tone` as a
 * register a *person chose*: `reset` reads no prior row and so has no register
 * to keep, which is why the endpoint refuses it alone, and the contract has
 * **withdrawn** the instruction to forward `GET /api/doc`'s own `tone`, because
 * nothing validates a register value and a client that guesses is handed the
 * wrong one rather than refused.
 *
 * Written once, and a test asserts that by reading every file under `src/`: the
 * server type-checks neither field, so the *string* `"false"` is a reset that
 * discards a book, and a second call site is a second chance to send one.
 */
async function reExtract(
  set: (partial: Partial<Store>) => void,
  get: () => Store,
  tone: string | null,
): Promise<void> {
  const where = get().shown() ?? get().at
  if (!where || get().running) return
  set({ running: true })
  try {
    const r = await api.postExtract(
      tone === null ? { ...where } : { ...where, reset: true, tone },
    )
    get().say(
      `  ${r.segments} segments, ${r.reused} reused` +
      (r.rejected ? `, ${r.rejected} stale proposal(s) refused` : ''),
      'good',
    )
    // Three annotations rather than three buckets — a segment can be `ambiguous`
    // *and* one of the other two, so they are reported a line each and never
    // summed.
    if (r.kept.length) {
      get().say(
        `  ${r.kept.join(', ')}: kept a stored target whose placeholders no longer ` +
        `match this document — fix the wording, or re-draft the row on its own`,
        'bad',
      )
    }
    if (r.replaced.length) {
      get().say(
        `  ${r.replaced.join(', ')}: a banked wording replaced a machine draft that no ` +
        `longer fits; their origin is now tm, and a hold on one of them is gone`,
        'warn',
      )
    }
    if (r.ambiguous.length) {
      get().say(
        `  ${r.ambiguous.join(', ')}: the position diff could not place these — a ` +
        `paragraph that moved, a new occurrence of a sentence this document already ` +
        `had, or a member of a run of identical paragraphs that changed size. They ` +
        `took the last stored wording under their key, without its hold, so check ` +
        `their wording and origin`,
        'warn',
      )
    }
    if (r.waived_source.length) {
      get().say(
        `  ${r.waived_source.join(', ')}: took a banked wording a reviewer waived ` +
        `where it was committed. The waiver did not travel — these arrive unwaived ` +
        `and the finding is reported here for you to decide`,
        'warn',
      )
    }
    await get().reloadState()
    // `open()` and not `refresh()`, and it is load-bearing rather than tidy: ids
    // are reassigned from `s0001` on every parse, so a leftover edit keyed
    // `s0007` would be written against whatever now sits there — and a token
    // hashes an absent target and an empty one alike, so between two
    // untranslated segments the lost-update check cannot catch it either.
    await get().open(where.src, where.lang)
  } catch (e) {
    get().say('  ' + reason(e), 'bad')
  } finally {
    set({ running: false })
  }
}

export const useStore = create<Store>()((set, get) => ({
  boot: 'loading',
  bootError: '',
  serverContract: null,
  state: null,

  at: null,
  doc: null,
  docLoading: false,
  docError: '',

  running: false,
  runCost: null,

  log: [],
  filter: 'all',
  focused: null,

  provider: '',
  model: '',
  listedProvider: '',
  models: null,
  modelsLoading: false,
  limit: 0,

  say: (text, level = 'plain', head = false) =>
    set(s => {
      line += 1
      const next = [...s.log, { n: line, text, level, head }]
      return { log: next.length > LOG_CAP ? next.slice(next.length - LOG_CAP) : next }
    }),

  clearLog: () => set({ log: [] }),
  setFilter: filter => set({ filter }),
  setFocused: focused => set({ focused }),
  setLimit: limit => set({ limit }),
  setModel: model => set({ model }),

  /**
   * Choosing a backend **clears the model**, and the listing is re-fetched
   * behind a sequence token.
   *
   * A listing against a black-holed backend can take most of a minute and a
   * person can change the select twice in that time. Without the token the last
   * reply to *arrive* wins and the field ends up holding one backend's models
   * while the select reads another — the stale-model hazard, arriving through a
   * different door.
   */
  chooseProvider: name => {
    set({ provider: name, model: '', listedProvider: '', models: null })
    void get().loadModels()
  },

  bootstrap: async () => {
    set({ boot: 'loading', bootError: '' })
    let state: StateResponse
    try {
      state = await api.getState()
    } catch (e) {
      set({ boot: 'failed', bootError: reason(e) })
      return
    }
    // **The whole reason `contract_version` exists.** A client reads it at
    // startup and refuses a number it does not know, rather than half-working
    // against a surface that has moved underneath it. A hard stop, not a
    // degradation: the failure a degrading client produces is silent.
    if (state.contract_version !== CONTRACT_VERSION) {
      set({ boot: 'incompatible', serverContract: state.contract_version, state })
      return
    }
    set({ boot: 'ready', state })
    // Said rather than swallowed. `store.doc_id` flattens every character
    // outside A-Za-z0-9._- , so a Chinese-titled library collapses onto one
    // identity and the rest of it is simply absent from the list whose job is to
    // say what there is.
    for (const c of state.collisions) {
      get().say(
        `${c.paths.join(' = ')} share one identity — ` +
        (c.offered ? `offering ${c.offered}` : 'no entry was offered for it'),
        'warn',
      )
    }
    // Fired after the bootstrap and deliberately not awaited with it: it leaves
    // the machine and can block for most of a minute against an unreachable
    // backend. The document list must not wait on that.
    void get().loadModels()
  },

  reloadState: async () => {
    try {
      set({ state: await api.getState() })
    } catch (e) {
      get().say(reason(e), 'bad')
    }
  },

  absorb: (providers, routing) =>
    set(s => (s.state ? { state: { ...s.state, providers, routing } } : {})),

  loadModels: async () => {
    const mine = ++modelSeq
    const provider = get().provider
    set({ modelsLoading: true })
    let models: ModelsResponse
    try {
      models = await api.getModels(provider)
    } catch (e) {
      // A transport failure, not a listing failure — the endpoint answers 200
      // even when the backend is unreachable. Shaped like the reply so the
      // control has one thing to read.
      models = { provider, configured: '', models: [], error: reason(e) }
    }
    if (mine !== modelSeq) return // superseded by a later provider change
    set({ models, modelsLoading: false, listedProvider: models.provider || '' })
    if (models.error) get().say('  models: ' + models.error, 'warn')
  },

  settled: () => same(get().at, get().shown()),

  shown: () => {
    const doc = get().doc
    return doc ? { src: doc.source, lang: doc.lang } : null
  },

  open: async (src, lang) => {
    // **Cleared before the fetch, not after.** `at` is already the new document
    // by this line, so an edit left in `drafts` is keyed on ids belonging to the
    // old one and the next save would post them under the new address. On the
    // success path the two spellings are indistinguishable; on a failed fetch
    // the old one leaves exactly that cross-document write armed.
    drafts.clear()
    set({ at: { src, lang }, docLoading: true, docError: '', focused: null })
    try {
      const doc = await api.getDoc({ src, lang })
      // Someone may have opened another document while this was in flight.
      if (!same(get().at, { src, lang })) return
      set({ doc, docLoading: false })
    } catch (e) {
      if (!same(get().at, { src, lang })) return
      set({ docLoading: false, docError: reason(e) })
      get().say(reason(e), 'bad')
    }
  },

  refresh: async () => {
    const where = get().shown()
    if (!where) return
    try {
      const doc = await api.getDoc(where)
      // Only if the ledger is still showing the document this refresh was
      // about. Compared **by value**: `open()` mints a fresh address on every
      // call, so an identity test would call the same document a different one.
      if (!same(get().shown(), where) && !same(get().at, where)) return
      set({ doc })
    } catch (e) {
      get().say(reason(e), 'bad')
    }
  },

  /**
   * Flush every dirty row. Returns false when something was held back or
   * refused, so a caller about to do something destructive can stop.
   */
  save: async () => {
    const where = get().shown()
    if (!where || !drafts.size()) return true
    const doc = get().doc
    if (!doc) return true

    // Blank edits are held back rather than sent. The server refuses an empty
    // target for the WHOLE request, and this is one map for the whole ledger —
    // so one cleared segment would refuse every later edit in the same session,
    // over and over, because the poisoned batch keeps being resent. Held back,
    // the good edits go through and the blank one is named once.
    const held: string[] = []
    const targets: Record<string, string> = {}
    for (const [id, text] of drafts.entries()) {
      if (text.trim()) targets[id] = text
      else held.push(id)
    }
    for (const id of held) {
      get().say(
        `  ${id} is empty — an empty target is not storable; restore the wording, ` +
        `or re-draft this segment to have it done again`,
        'bad',
      )
    }
    const ids = Object.keys(targets)
    if (!ids.length) return !held.length

    // The token each edit was based on, so a translation batch that landed
    // mid-review — or a second window — cannot overwrite a sentence silently.
    // Per id and optional: a segment we hold no token for is written the way it
    // always was.
    const base: Record<string, string> = {}
    const byId = new Map(doc.segments.map(s => [s.id, s]))
    for (const id of ids) {
      const token = byId.get(id)?.token
      if (token) base[id] = token
    }

    try {
      const r = await api.postSave({ ...where, targets, base })
      // Only what was sent. A held-back blank stays dirty and stays on screen.
      drafts.forget(ids)
      const lost = Object.keys(r.conflicts)
      if (lost.length) {
        get().say(
          `  ${lost.join(', ')} changed underneath this edit and were not written — ` +
          `the wording on screen for them is now the stored one`,
          'bad',
        )
      }
      if (r.unknown.length) {
        get().say(`  ${r.unknown.join(', ')} name no segment and were ignored`, 'warn')
      }
      await get().refresh()
      return !held.length && !lost.length
    } catch (e) {
      // Cleared only on success: an empty target is a 400, and clearing first
      // would throw away every other edit in the batch along with the one the
      // server would not take.
      get().say('  ' + reason(e), 'bad')
      return false
    }
  },

  /**
   * Start a run and follow it to a terminal state.
   *
   * `ids` names segments and **outranks everything else in the selection** — the
   * mode table, the hold exclusion and the pre-filter that drops segments a
   * person wrote. `mode` still chooses the routing stage and the prompt, so it
   * is sent either way.
   */
  runJob: async (mode, ids, overwriteHuman, note) => {
    if (!get().settled() || get().running) return
    const where = get().shown()
    if (!where) return

    // **Raised before the first await.** The `running` test above is only a
    // guard if nothing can pass it twice, and `save()` below is a full round
    // trip whenever anything is dirty — with every run control still enabled.
    // Two clicks in that window started two runs, and two `llm:*` writes to one
    // segment are last-write-wins with no token and no check.
    set({ running: true })

    const bound = get().limit
    // The bound is named in the header rather than after the fact, so a reader
    // scrolling back knows why a run stopped where it did. A named `ids` says so
    // instead of naming the bound, because the bound is documented as not
    // applying there and is not sent.
    get().say(
      ids
        ? `— ${mode} · ${ids.join(', ')}` +
          (overwriteHuman ? ' · replacing wording a person wrote' : '') +
          (note ? ' · ' + note : '')
        : `— ${mode}${bound ? ` · at most ${bound}` : ''} —`,
      'plain',
      true,
    )

    try {
      await get().save()
      // **Neither key is sent unless it was chosen.** With both omitted the
      // server resolves this stage's own routing entry, which is what makes one
      // pair of controls safe on a bar with three run buttons — a seeded value
      // in either box is draft's answer riding on Polish and Repair.
      const model = get().model
      const provider = get().provider || (model ? get().listedProvider : '')
      const job = await api.postTranslate({
        ...where,
        mode,
        ...(ids ? { ids } : {}),
        ...(overwriteHuman ? { overwrite_human: true } : {}),
        ...(provider ? { provider } : {}),
        ...(model ? { model } : {}),
        // Sent as a number, never an option's string, and omitted at 0.
        // **Not sent at all beside `ids`**: the bound is documented as ignored
        // there and is still checked, so the only thing sending it can do is
        // refuse a request that would have ignored it.
        ...(!ids && bound ? { limit: bound } : {}),
      })
      // What the server actually resolved — the only place this answer appears
      // outside a log line the contract forbids parsing.
      get().say(
        '  ' + (job.route.error || `${job.route.provider} · ${job.route.model || 'default'}`),
        job.route.error ? 'warn' : 'plain',
      )
      if (!job.total) {
        get().say('  nothing to do', 'warn')
        return
      }

      let seen = 0
      for (;;) {
        await new Promise(r => { setTimeout(r, 700) })
        const answer = await api.postJob(job.id)
        // A job record, or a 200 carrying `error` alone. Told apart by the
        // **absence of `done`**, never by the presence of `error`: a live record
        // carries `error: null` throughout, and a failed one carries a sentence
        // while still being a record.
        if (!isJobRecord(answer)) {
          get().say('  ' + answer.error, 'bad')
          break
        }
        for (const l of answer.log.slice(seen)) get().say('  ' + l)
        seen = answer.log.length
        if (!answer.done) continue

        for (const [id, why] of answer.failures) {
          get().say(`  unresolved ${id}: ${why}`, 'bad')
        }
        if (answer.refused.length) {
          get().say(
            `  ${answer.refused.join(', ')}: a person wrote this, so the run was not ` +
            `allowed to replace it — the model was still called and still cost tokens. ` +
            `Re-draft the row and confirm, or ` +
            `\`lx translate ${where.src} --lang ${where.lang} ` +
            `--ids ${answer.refused.join(',')} --overwrite-human\`.`,
            'bad',
          )
        }
        if (answer.error) get().say('  ' + answer.error, 'bad')
        // The five integers, kept structurally. The run has already *said* what
        // it cost, once, through `translate.usage_line` on the same `progress`
        // sink `lx` prints from — so nothing here words that sentence a second
        // time. This is the field the contract added precisely because a client
        // may not parse the log.
        set({ runCost: { usage: answer.usage, at: where.src, lang: where.lang } })
        break
      }

      // A run that finishes after somebody opened another document must not
      // repaint that one and have its tally read as this run's result.
      if (same(get().at, where)) await get().refresh()
      else get().say(`  ${where.src} [${where.lang}] finished; reopen it to see the result`, 'warn')
      await get().reloadState()
    } catch (e) {
      get().say('  ' + reason(e), 'bad')
    } finally {
      set({ running: false })
    }
  },

  setHold: async (ids, held) => {
    const where = get().shown()
    if (!where || !ids.length) return
    try {
      const r = await api.postHold({ ...where, ids, held })
      if (r.unknown.length) get().say(`  ${r.unknown.join(', ')} name no segment`, 'warn')
      await get().refresh()
    } catch (e) {
      get().say('  ' + reason(e), 'bad')
    }
  },

  setWaive: async (ids, waived) => {
    const where = get().shown()
    if (!where || !ids.length) return
    try {
      const r = await api.postWaive({ ...where, ids, waived })
      if (r.unknown.length) get().say(`  ${r.unknown.join(', ')} name no segment`, 'warn')
      // A waiver is pinned to the wording it was granted over. An id here was
      // left alone because the stored target moved between the request being
      // prepared and the write — a batch or another client landing in between.
      if (r.stale.length) {
        get().say(
          `  ${r.stale.join(', ')}: the wording changed while this was in flight, ` +
          `so the waiver was not applied — read it again and decide`,
          'warn',
        )
      }
      await get().refresh()
    } catch (e) {
      get().say('  ' + reason(e), 'bad')
    }
  },

  check: async () => {
    const where = get().shown()
    if (!where) return
    await get().save()
    try {
      const r = await api.postCheck(where)
      const waived = r.by_rule['waived'] ?? 0
      get().say(
        `check: ${r.errors} error${r.errors === 1 ? '' : 's'}, ` +
        `${r.warnings} warning${r.warnings === 1 ? '' : 's'}` +
        (waived ? `, ${waived} waived` : ''),
        r.errors ? 'bad' : 'good',
      )
      await get().refresh()
    } catch (e) {
      get().say('  ' + reason(e), 'bad')
    }
  },

  extract: () => reExtract(set, get, null),
  startOver: register => reExtract(set, get, register),

  render: async () => {
    const where = get().shown()
    if (!where) return
    await get().save()
    try {
      const r = await api.postRender({ ...where, fallback: false })
      get().say(
        `wrote ${r.wrote}` + (r.missing ? ` (${r.missing} without a usable translation)` : ''),
        'good',
      )
    } catch (e) {
      get().say('  ' + reason(e), 'bad')
    }
  },

  commit: async () => {
    const where = get().shown()
    if (!where) return
    await get().save()
    try {
      const r = await api.postCommit(where)
      // Not the count alone. `committed` is not a complete report: held,
      // stranded and failing segments are declined, and a reviewer told only
      // "+= 12" cannot act on the forty that were not.
      const left = [
        r.refused.length ? `${r.refused.length} failing` : '',
        r.stranded.length ? `${r.stranded.length} on an older numbering` : '',
        r.held.length ? `${r.held.length} held` : '',
      ].filter(Boolean)
      get().say(
        `translation memory += ${r.committed}` +
        (left.length ? ` — not banked: ${left.join(', ')}` : ''),
        'good',
      )
      if (r.stranded.length) {
        get().say(
          `  ${r.stranded.join(', ')}: this wording speaks a numbering the document has ` +
          `moved on from. It renders as written, so nothing is broken — but banked it ` +
          `would shadow a correct record. Re-word the segment against the source as it ` +
          `stands now`,
          'warn',
        )
      }
    } catch (e) {
      get().say('  ' + reason(e), 'bad')
    }
  },
}))

// ── selectors, kept beside the store so a component imports one thing ──────

/** The segments a filter admits. Pure, and it computes nothing the server
 *  owns — every predicate here reads a field the segment already carries. */
export function visible(doc: DocResponse | null, filter: Filter): Segment[] {
  if (!doc) return []
  switch (filter) {
    case 'pending': return doc.segments.filter(s => !s.target)
    case 'failing': return doc.segments.filter(s => s.issues.some(isError))
    case 'held': return doc.segments.filter(s => s.review === 'held')
    case 'waived': return doc.segments.filter(s => s.waived)
    case 'all': return doc.segments
  }
}

/** `ApiError` when the server refused, so a caller can branch on the status.
 *  `403` means never writable whatever the value; `400` means fix the payload
 *  and send it again. The sentence is for a person and is never parsed. */
export const statusOf = (e: unknown): number | null =>
  e instanceof ApiError ? e.status : null

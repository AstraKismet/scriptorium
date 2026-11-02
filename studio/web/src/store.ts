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
  type Provider, type Routing, type SaveResponse, type Segment, type StateResponse, type Usage,
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
  /**
   * Whether `docError` is the server's own refusal to read `at` — not this page
   * declining to leave a document whose words it could not write, and not a
   * request that never reached the server. They draw on one screen and mean
   * different things: only an answered refusal can be a file nobody has
   * extracted yet, and only that screen offers the extract.
   */
  readFailed: boolean

  /** One run at a time, and every entry point reads it. */
  running: boolean
  /** Which run `running` is about, in words a refusal can name — `''` while
   *  nothing runs. */
  runName: string
  /**
   * Why an act the reviewer just pressed did not run, said where they are
   * looking — beside the control — and not only as a line in a drawer that may
   * be closed. Set by the act that declined; cleared when it is dismissed, when
   * that act is pressed again, when another document is put on screen, or when
   * the run it names ends — after that "let the run finish" is no longer true,
   * and the log keeps the line.
   */
  notice: string
  runCost: RunCost | null

  log: LogLine[]
  filter: Filter
  // **No `focused` here, on purpose.** Which segment a reviewer is on lives in
  // the address and nowhere else — `routes.useFocused`, `routes.focus`. A copy
  // here, reconciled with the address by effects, is what blanked the page on a
  // second click (HANDOFF-084).

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
  setNotice: (text: string) => void
  clearLog: () => void
  setFilter: (f: Filter) => void
  setLimit: (n: number) => void
  setModel: (m: string) => void
  chooseProvider: (name: string) => void

  bootstrap: () => Promise<void>
  /** True when the projection was read; a failure is logged and leaves it. */
  reloadState: () => Promise<boolean>
  /** Take the two projections a `POST /api/config` reply carries back.
   *
   *  A settings screen repaints from its own write's answer and **never** calls
   *  `GET /api/state` to repaint a form: that endpoint loads every segment of
   *  every document to answer, which is a strange price for redrawing one
   *  dropdown. */
  absorb: (providers: Provider[], routing: Routing) => void
  loadModels: () => Promise<void>
  /**
   * Show a document, writing out whatever is unsaved in the one on screen first.
   *
   * It may decline: while it still holds wording it could not write, it leaves
   * `doc` where it is and says why in `docError` rather than discarding the
   * words. Opening the document those words belong to is never declined.
   */
  open: (src: string, lang: string) => Promise<void>
  /** Re-read the document on screen. True when it read it and it is still the
   *  one on screen; false when the read failed or the page moved meanwhile. */
  refresh: () => Promise<boolean>
  /** True when the ledger is showing the document `at` names and no `open()` is
   *  reading a document to replace it. A `refresh()` in flight does not count —
   *  ordering the page's reads is HANDOFF-096's. */
  settled: () => boolean
  /** The address of what is on screen — read from `doc`, never from `at`, so
   *  the two cannot disagree about which document an act is addressed to. */
  shown: () => DocAddress | null

  save: () => Promise<boolean>
  /**
   * `save()`, answered with what it left unwritten and why — the three reasons
   * have three different remedies, and a caller about to act on the document
   * needs to know which one it is looking at (HANDOFF-092). A blank is in none
   * of the lists: it is not wording.
   */
  writeOut: () => Promise<Unwritten>
  /** Decline an act for `d` (see `declines`): said in the log and beside the
   *  bar, and taken down once it is no longer true. True when it declined;
   *  `null` declines nothing. */
  decline: (d: Declined | null) => boolean
  runJob: (mode: Mode, ids?: string[], overwriteHuman?: boolean, note?: string) => Promise<void>
  /** Pick up a run this page started before it was reloaded. */
  resume: () => Promise<void>
  /** Stop following a run. It is **not** cancelled — nothing can cancel one. */
  abandon: () => void
  setHold: (ids: string[], held: boolean) => Promise<void>
  setWaive: (ids: string[], waived: boolean) => Promise<void>
  check: () => Promise<void>
  /**
   * Read a source and parse it, keeping the frozen register if it has one.
   *
   * **The document is named by the caller**, never read off the screen: the
   * toolbar names the one its confirmation named, and the page for a file
   * nobody has extracted names that file — which is not on screen at all, and
   * cannot be until this has made it exist. It used to read `shown()`, and the
   * rail's *Not yet extracted* entry re-extracted whatever document was open
   * (HANDOFF-088). True when the server accepted it.
   */
  extract: (where: DocAddress) => Promise<boolean>
  /**
   * The extract the page for a file nobody has extracted offers — after asking
   * the server again whether that is still true.
   *
   * The offer rests on a read, and a read can be as old as the page: `at`
   * outlives a trip to `#/backends`, so coming back reads nothing, and a click
   * on the rail entry for the page already shown is the same address and reads
   * nothing either. A terminal can extract and translate the file in that time,
   * and the click would then be an unconfirmed re-extract of a translated
   * document. So the click asks first — the toolbar's Re-extract re-reads
   * before deciding its confirmation for the same reason.
   *
   * **What it asks is the server's own list, not the document.** A failed read
   * of the document cannot answer: `web/server.py` turns every exception on a
   * read into a 400, so a locked database looks exactly like a file with no
   * state, and the page may not parse the sentence. `GET /api/state`'s
   * `untracked` is computed now, by the server, from the identity state is keyed
   * on — the fact the no-confirmation argument needs, fresh. A file it no
   * longer lists is not extracted, and is re-read where the address still names
   * it. True when an extract was sent and accepted.
   */
  extractUntracked: (where: DocAddress) => Promise<boolean>
  /**
   * Discard everything and re-extract in a register a person chose.
   *
   * A separate action rather than a flag on `extract`, so that the two spellings
   * the server type-checks with nothing — `reset` and `tone` — are written in
   * exactly one place in this whole source tree. A test asserts that by reading
   * the files: any second call site is a second chance to send the *string*
   * `"false"`, which is truthy in Python and discards a book.
   */
  startOver: (where: DocAddress, register: string) => Promise<boolean>
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

/**
 * Which call of `open()` is the current one.
 *
 * Not `at`: `at` names a *document*, and the same document can be asked for
 * twice while the first call is still in flight — A, B, C, back to B. Compared
 * by value, the first call for B then passed every "am I still the current
 * open" test with the `leaving` it captured before C existed: its fetch cleared
 * the words typed in C, which the second call for B had just declined to leave
 * over, and its refusal or a late failure landed over the second call's page.
 * A number per call has no second instance. Found by the third review of
 * HANDOFF-088; the first half is older than it.
 */
let openSeq = 0

/**
 * Moves when a save sends wording and again when its reply lands. An act whose
 * confirmation depends on what the document holds — the toolbar's Re-extract —
 * records it before it reads the count and refuses if it moved: the count can
 * be older than the reviewer's own write however the reads came back, so
 * whether anything was written is asked directly rather than inferred from the
 * order of the reads. Ordering the page's reads was tried four ways in
 * HANDOFF-088's reviews and each left an ordering uncovered; that problem is
 * HANDOFF-096's, and this does not depend on it.
 */
let writes = 0

/** The write epoch; see `writes`. */
export const writeEpoch = (): number => writes

/**
 * What one round of writing came to. `failed` is a request that did not answer
 * or answered with something this page cannot read — as distinct from words
 * held back on purpose (a blank, a wording waiting on the reviewer's choice),
 * which is not a failure and must not be reported as one.
 */
interface Outcome { ok: boolean; failed: boolean }

/**
 * What a save left holding wording that exists only on this page, by why.
 *
 * - `failed`: a request failed, or answered with something this page cannot
 *   read. The log says why; the next save sends the wording again.
 * - `waiting`: refused as stale, and waiting in its row for the reviewer to
 *   choose between their wording and the stored one (HANDOFF-091). Safe where it
 *   is; nothing sends it until they choose.
 * - `newer`: typed while the save was on the wire — into a row that save
 *   carried, or into one it never saw. Nothing failed; the next save writes it.
 * - `stranded`: a re-extract this page asked for has renumbered the ids, so
 *   nothing can write it; it goes, named, when the new parse is on screen. Its
 *   own list, because "the log says why, press it again" is false of it (found
 *   by the review of HANDOFF-092).
 *
 * A blank is in none of them, because a blank is not wording — the rule `open()`
 * has followed since 2026-09-20, and that the toolbar's Re-extract did not
 * (HANDOFF-092).
 */
export interface Unwritten {
  failed: string[]
  waiting: string[]
  newer: string[]
  stranded: string[]
}

/** A refusal, and the rows and reason it is about — so the notice it puts up
 *  can be taken down the moment it stops being true, and not before. */
export interface Declined {
  text: string
  ids: string[]
  why: 'failed' | 'waiting' | 'newer' | 'stranded'
}

/**
 * **One save on the wire at a time.** HANDOFF-091: the field's blur and the
 * act that took focus from it — Ctrl+Enter's move to the next row, any toolbar
 * button, a rail click, Back pressed twice — each called `save()`, and a second
 * call made while the first was in flight sent the same wording again under the
 * same `base`. The server's compare-and-swap then refused the second as a
 * conflict carrying the very words the first had written, and the page said in
 * red that a write which succeeded had not. Matching the twin by text cannot
 * work here: the server normalizes what it stores, so the conflict's text and
 * the text sent differ for ordinary zh-TW input. Not sending the duplicate is
 * the one rule that holds.
 *
 * A call made while a round is on the wire waits for it and then runs one more
 * round, shared by every caller that asked meanwhile, which reads `drafts` when
 * it starts — so what it sends is what is still unwritten then. Keyed on the
 * drafts epoch: a document replaced under a round in flight starts a fresh
 * line, so a request that never answers holds up nothing on the next document.
 */
let onWire: Promise<void> | null = null
let onWireEpoch = -1
let queued: Promise<Outcome> | null = null

/**
 * The files an `extractUntracked` is asking the server about. A second click on
 * the same file in the same moment is the same act, not a second one, and says
 * nothing. Per file, because a single flag swallowed a click on a *different*
 * file made while the first was asking — no request and no line, the symptom
 * this package exists for (found by its fourth review).
 */
const asking = new Set<string>()

const same = (a: DocAddress | null, b: DocAddress | null): boolean =>
  !!a && !!b && a.src === b.src && a.lang === b.lang

const reason = (e: unknown): string =>
  e instanceof Error ? e.message : String(e)

/**
 * Whether an edit holds wording at all.
 *
 * One predicate and not two spellings of it: `save()` sends these and holds the
 * rest back, because the server refuses an empty target for the **whole**
 * request, and `open()` asks the same question to decide whether leaving would
 * cost anybody a sentence. A cleared field is a gesture the server will not
 * store; it is not something somebody wrote.
 */
const wording = (text: string): boolean => text.trim() !== ''

/** The ids whose words exist nowhere but in this page. */
const unwritten = (): string[] =>
  drafts.entries().filter(([, text]) => wording(text)).map(([id]) => id)

/** Whether anything on this page holds wording no save has written. Exported
 *  so that an act deciding whether it may go ahead asks the predicate `save()`
 *  and `open()` ask, rather than `drafts.size()`, which counts a blank. */
export const holdsWording = (): boolean => unwritten().length > 0

/** The same ids, for a dialog that has to name what it would discard. */
export const unwrittenIds = (): string[] => unwritten()

/** What is left after a round whose outcome was `failed` or not. */
function left(failed: boolean): Unwritten {
  const ids = unwritten()
  if (drafts.stranded()) return { failed: [], waiting: [], newer: [], stranded: ids }
  const waiting = ids.filter(id => drafts.conflict(id))
  const rest = ids.filter(id => !drafts.conflict(id))
  return failed
    ? { failed: rest, waiting, newer: [], stranded: [] }
    : { failed: [], waiting, newer: rest, stranded: [] }
}

const rows = (ids: string[]): string => (ids.length === 1 ? 'its row' : 'their rows')

/**
 * Why an act that rests on what the server holds may not go ahead after a save,
 * or `null` when it may. One sentence per act, because what the act would do
 * without the words is what makes the refusal worth reading.
 *
 * `failed`, `stranded` and `newer` stop every act: in each the wording exists
 * only here and the act would be done to the document without it. `newer` was
 * first let through, on the argument that a row typed into during its save was
 * written in that save — true of a row the save carried, false of one it never
 * saw, and a run then drafted a sentence the reviewer was typing (found by the
 * review of HANDOFF-092). Nothing failed there, so its sentence says so and a
 * second press writes the words first. `waiting` stops only the acts whose
 * output stands for the document — the file and the memory — because the
 * document's wording at that row is not decided yet; a run, a check and the
 * reading view go ahead over it, since the reviewer's words are safe in the row
 * and nothing sends them until they choose.
 */
export function declines(
  what: Unwritten,
  refusal: string,
  src: string,
  without: string,
  decided = false,
): Declined | null {
  if (what.stranded.length) {
    const ids = what.stranded
    return {
      ids, why: 'stranded',
      text: `${refusal}: a re-extract has just renumbered the segments of ${src}, so ` +
        `${names(ids)} can no longer be written anywhere. ${ids.length === 1 ? 'It goes' : 'They go'} ` +
        `when the new parse is on screen, and the log names ${ids.length === 1 ? 'it' : 'them'} then.`,
    }
  }
  if (what.failed.length) {
    const ids = what.failed
    return {
      ids, why: 'failed',
      text: `${refusal}: ${names(ids)} could not be written to ${src} — the log says why — ` +
        `and ${without}. The wording is still in ${rows(ids)}; press it again once it is written.`,
    }
  }
  if (what.newer.length) {
    const ids = what.newer
    return {
      ids, why: 'newer',
      text: `${refusal}: ${names(ids)} changed while this was saving and ` +
        `${ids.length === 1 ? 'is' : 'are'} not written yet — nothing failed, but ${without}. ` +
        `Press it again, which writes ${ids.length === 1 ? 'it' : 'them'} first.`,
    }
  }
  if (decided && what.waiting.length) {
    const ids = what.waiting
    return {
      ids, why: 'waiting',
      text: `${refusal}: ${names(ids)} changed underneath your edit and ` +
        `${ids.length === 1 ? 'waits' : 'wait'} on your choice in ${rows(ids)}, so what ${src} ` +
        `says there is not decided yet. Choose which wording stays, then press it again.`,
    }
  }
  return null
}

/**
 * The notice a save, or an act declining over unwritten wording, put beside the
 * bar — with the rows and the reason it is about, and the parse those rows
 * belong to.
 *
 * A save that fails says why in the log at `bad`, and the log is a drawer that
 * may be shut — while the field the reviewer just left, or moved off with
 * Ctrl+Enter, gives no sign beyond a dot that its wording went nowhere. So it is
 * said where the reviewer is looking. **And it is taken down exactly when it
 * stops being true, and not by anything else** (`stillTrue`): the first version
 * cleared it on any readable save and on a run's end, and the review found it
 * wrong both ways — cleared while a row still waited on the reviewer's choice or
 * still held words that had failed, kept after the field was emptied, the row's
 * wording discarded, or the map stranded by a re-extract.
 */
let said: (Declined & { epoch: number }) | null = null

/** Whether `said` still describes the page. A readable save answers `failed`
 *  and `newer` itself, in `round()`. */
function stillTrue(s: Declined & { epoch: number }): boolean {
  if (drafts.epoch() !== s.epoch) return false
  if (s.why === 'stranded') return drafts.stranded()
  if (drafts.stranded()) return false
  const holding = s.ids.filter(id => wording(drafts.get(id) ?? ''))
  if (s.why === 'waiting') return holding.some(id => drafts.conflict(id))
  return holding.some(id => !drafts.conflict(id))
}

/** Take `said` down if it is no longer true — or forget it if something else
 *  has replaced it beside the bar. */
function recheck(get: () => Store): void {
  if (!said) return
  if (get().notice !== said.text) { said = null; return }
  if (!stillTrue(said)) {
    said = null
    get().setNotice('')
  }
}

/** Put `d` beside the bar as `said`. */
function sayBeside(get: () => Store, d: Declined): void {
  said = { ...d, epoch: drafts.epoch() }
  get().setNotice(d.text)
}

/** Whether the notice beside the bar is one `said` is keeping — which a run's
 *  end must leave alone. */
const keeping = (get: () => Store): boolean => !!said && get().notice === said.text

/** A run has ended: "let the run finish" is no longer true, so its notice goes
 *  — but not one about unwritten wording that still holds. */
function endOfRun(get: () => Store): void {
  if (!keeping(get)) get().setNotice('')
}

/** Decline an act for `d`, said in the log and beside the bar. True when it
 *  declined; `null` declines nothing. */
function stopped(get: () => Store, d: Declined | null): boolean {
  if (!d) return false
  get().say('  ' + d.text, 'warn')
  sayBeside(get, d)
  return true
}

/**
 * Name a list of segment ids in a log line, bounded.
 *
 * `ambiguous` is documented as **not capped**: past the alignment work budget
 * the position diff is skipped for the whole document and every carried segment
 * lands in it, which on a novel that is one sentence repeated is every segment
 * in the book. A line naming five thousand ids is not a line anybody reads.
 */
export function names(ids: string[], most = 20): string {
  if (ids.length <= most) return ids.join(', ')
  return `${ids.slice(0, most).join(', ')} and ${ids.length - most} more`
}

/**
 * The job this page is following, kept where a reload can find it.
 *
 * One run at a time is a **correctness** rule here rather than tidiness — two
 * `llm:*` writes to one segment are last-write-wins with no token and no check —
 * and a flag in memory cannot enforce it across F5. Nothing on the wire lists
 * running jobs (`GET /api/state` carries no job at all and `POST /api/job` needs
 * an id you already hold), so the id has to survive the reload or the guard is
 * gone with the page. `sessionStorage` and not `localStorage`: a job dies with
 * the server process, so an id from last week is an id for "no such job".
 *
 * It is honest about what it does *not* fix: a second tab, or `lx translate` in
 * a terminal, is still a second writer and nothing here can see it.
 */
const HELD_JOB = 'scriptorium.job'

interface HeldJob { id: string; src: string; lang: string }

let abandoned = false

function remember(job: HeldJob): void {
  try { sessionStorage.setItem(HELD_JOB, JSON.stringify(job)) } catch { /* private mode */ }
}

function forgetJob(): void {
  try { sessionStorage.removeItem(HELD_JOB) } catch { /* private mode */ }
}

function remembered(): HeldJob | null {
  try {
    const raw = sessionStorage.getItem(HELD_JOB)
    if (!raw) return null
    const held = JSON.parse(raw) as HeldJob
    return held.id && held.src && held.lang ? held : null
  } catch {
    return null
  }
}

/**
 * Follow a run to a terminal state.
 *
 * **A failed poll is not the end of a run.** `protocol_version` is HTTP/1.0, so
 * every response closes the connection and there is no keep-alive; an occasional
 * failed poll is ordinary. The predecessor abandoned the run on the first one,
 * client-side, while the server thread kept writing — and the page then showed a
 * document it had stopped following. So a failure is retried, and only a run of
 * them gives up, with a sentence saying the run is still going.
 */
async function follow(
  set: (partial: Partial<Store>) => void,
  get: () => Store,
  id: string,
  where: DocAddress,
): Promise<void> {
  let seen = 0
  let misses = 0
  abandoned = false
  for (;;) {
    await new Promise(r => { setTimeout(r, 700) })
    if (abandoned) return
    let answer
    try {
      answer = await api.postJob(id)
    } catch (e) {
      misses += 1
      if (misses < 10) continue
      get().say(
        `  gave up polling after ${misses} failed attempts (${reason(e)}). The run has ` +
        `not been cancelled — re-open the document to see what it wrote.`,
        'bad',
      )
      return
    }
    misses = 0
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

    for (const [segId, why] of answer.failures) {
      get().say(`  unresolved ${segId}: ${why}`, 'bad')
    }
    if (answer.refused.length) {
      get().say(
        `  ${names(answer.refused)}: a person wrote this, so the run was not ` +
        `allowed to replace it — the model was still called and still cost tokens. ` +
        `Re-draft the row and confirm, or ` +
        `\`lx translate ${where.src} --lang ${where.lang} ` +
        `--ids ${answer.refused.join(',')} --overwrite-human\`.`,
        'bad',
      )
    }
    if (answer.error) get().say('  ' + answer.error, 'bad')
    // The five integers, kept structurally. The run has already *said* what it
    // cost, once, through `translate.usage_line` on the same `progress` sink
    // `lx` prints from — so nothing here words that sentence a second time. This
    // is the field the contract added precisely because a client may not parse
    // the log.
    set({ runCost: { usage: answer.usage, at: where.src, lang: where.lang } })
    break
  }

  // A run that finishes after somebody opened another document must not repaint
  // that one and have its tally read as this run's result.
  if (same(get().at, where)) await get().refresh()
  else get().say(`  ${where.src} [${where.lang}] finished; reopen it to see the result`, 'warn')
}

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
 *
 * **`where` is the caller's, and nothing here reads the screen to decide it.**
 * This function used to take `shown() ?? at`, and the rail's *Not yet extracted*
 * entry — which had just asked for a document the address did not name, and
 * lost it to `App.tsx`'s effect a render later — re-extracted the document that
 * was open while its log line named the file that was clicked (HANDOFF-088).
 * What the screen still decides is what the re-parse *does to this page*, and
 * that is two questions, each asked when the reply lands: whether the unsaved
 * words are keyed on the parse that has gone, and whether the document is the
 * one the page is addressed to.
 */
async function reExtract(
  set: (partial: Partial<Store>) => void,
  get: () => Store,
  where: DocAddress,
  tone: string | null,
): Promise<boolean> {
  if (get().running) return false
  set({ running: true, runName: `the extract of ${where.src}` })
  try {
    const r = await api.postExtract(
      tone === null ? { ...where } : { ...where, reset: true, tone },
    )
    // **The ids every unsaved edit is keyed on have just stopped existing** —
    // when, and only when, the document re-parsed is the one those edits were
    // typed in. That is `shown()`, read now: `drafts` holds the words of the
    // document on screen, and a re-parse of any other document renumbers
    // nothing they are keyed on. Marked anyway, they would go as "renumbered" by
    // a parse that never touched them — an extract from the page for a file
    // nobody has extracted leaves `doc` on the document before it, whose words
    // that page may be holding because it could not write them.
    //
    // It is said here rather than left for the re-open below to notice: what
    // follows is `reloadState()` and then a fetch, two round trips with the
    // ledger still mounted, so a keystroke arriving after the re-parse would be
    // indistinguishable from one typed against the parse that is gone. Emptying
    // the map would not hold for that reason; the mark does, until a parse the
    // entries were typed against is on screen.
    //
    // The toolbar's plain Re-extract never gets here holding anything — it saves
    // first and refuses the act while anything is left. Start over does: it asks
    // no such question, and its reviewer has confirmed discarding the document's
    // translations, not the sentence they were part-way through typing. So the
    // ids are named when they go rather than vanishing without a line.
    if (same(get().shown(), where)) drafts.strand()
    // A notice that wording is still in its row, or waits there, is false from
    // this moment: nothing can write it now (found by the review of HANDOFF-092).
    recheck(get)
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
        `  ${names(r.kept)}: kept a stored target whose placeholders no longer ` +
        `match this document — fix the wording, or re-draft the row on its own`,
        'bad',
      )
    }
    if (r.replaced.length) {
      get().say(
        `  ${names(r.replaced)}: a banked wording replaced a machine draft that no ` +
        `longer fits; their origin is now tm, and a hold on one of them is gone`,
        'warn',
      )
    }
    if (r.ambiguous.length) {
      get().say(
        `  ${names(r.ambiguous)}: the position diff could not place these — a ` +
        `paragraph that moved, a new occurrence of a sentence this document already ` +
        `had, or a member of a run of identical paragraphs that changed size. They ` +
        `took the last stored wording under their key, without its hold, so check ` +
        `their wording and origin`,
        'warn',
      )
    }
    if (r.waived_source.length) {
      get().say(
        `  ${names(r.waived_source)}: took a banked wording a reviewer waived ` +
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
    //
    // **Only while `at` still names it.** `at` is set from the address and
    // nothing else, so this reads what the address already asked for; it never
    // opens one the address does not name — that is `App.tsx`'s effect's, and a
    // second opener is how the rail's entry lost its document. So a reviewer who
    // moved on while the extract was in flight is not pulled back. `at` rather
    // than the screen, because the page for a file with no state — reached from
    // the rail or by a hand-typed link — leaves `doc` on the previous document
    // while `at` names the file, and once this has made the file exist, reading
    // it is exactly what that address was asking for. It is then a first read
    // and not a re-read, which is why this is `open()`.
    if (same(get().at, where)) await get().open(where.src, where.lang)
    return true
  } catch (e) {
    get().say('  ' + reason(e), 'bad')
    return false
  } finally {
    set({ running: false, runName: '' })
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
  readFailed: false,

  running: false,
  runName: '',
  notice: '',
  runCost: null,

  log: [],
  filter: 'all',

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
  setNotice: notice => set({ notice }),
  setFilter: filter => set({ filter }),
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
    // A run this page started before it was reloaded is still running, and the
    // one-run-at-a-time rule is a data rule rather than a UI one. Picking it up
    // again is the only way a reload does not silently license a second writer.
    void get().resume()
  },

  reloadState: async () => {
    try {
      set({ state: await api.getState() })
      return true
    } catch (e) {
      get().say(reason(e), 'bad')
      return false
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

  // Not while an `open()` is on its way to replace what is on screen: an act
  // taken then reads a snapshot the page is about to discard. "Draft again"
  // after a save, with Back and Forward pressed during it, sent a run built from
  // the origin before the save — no question about a person's wording, the
  // model billed, the write refused (found by HANDOFF-088's fifth review).
  settled: () => same(get().at, get().shown()) && !get().docLoading,

  shown: () => {
    const doc = get().doc
    return doc ? { src: doc.source, lang: doc.lang } : null
  },

  open: async (src, lang) => {
    const want = { src, lang }
    // The document the unsaved edits were typed in, read before anything moves.
    // It is `shown()` rather than `at` for the reason `save()` uses `shown()`:
    // `doc` is what the ledger drew, and the ids in `drafts` are that parse's.
    const leaving = get().shown()

    // **`at` is assigned before the first await — and `docLoading` is not.**
    // `App.tsx`'s effect re-runs on every render, because `addressed` is a fresh
    // object out of `routes.parse()`, and its comparison against `at` is the one
    // thing stopping it re-entering here; `save()` below sets state several
    // times. Raising `docLoading` with it would unmount the ledger, and the
    // textarea with it, before the flush has read the field — taking an IME
    // composition that has not yet produced an `input` event, in a workbench
    // that exists for writing Chinese.
    set({ at: want, docError: '', readFailed: false })
    const mine = ++openSeq
    const current = (): boolean => mine === openSeq

    // **Written before it is discarded.** A navigation that moves no DOM focus —
    // Back, Forward, a mouse side button, a hand-typed link — blurs no field, so
    // nothing had saved, and `beforeunload` does not fire for a fragment
    // navigation either: the words used to go with no request and no line in the
    // log. Nothing here asks which document is being opened, and it does not
    // need to: the flush is addressed to `shown()`, which is still the document
    // the words were typed in, so it is correctly addressed whoever called.
    //
    // **Two laps, and no more.** Words typed while the first lap was in flight
    // are newer than what it wrote, and stay unwritten after it (HANDOFF-091);
    // refusing over them would say "could not be written" about a write that
    // succeeded. So one more lap writes them, based on the token the first lap
    // produced. Typing that outlasts both laps is refused with a sentence saying
    // so — a lap per keystroke is a loop with no bound.
    const writable = !drafts.stranded()
    let lapFailed = false
    if (drafts.size() && writable) {
      lapFailed = (await flush(get)).failed
      if (!current()) return
      if (!lapFailed && unwritten().some(id => !drafts.conflict(id))) {
        lapFailed = (await flush(get)).failed
      }
    }

    // **A superseded open stops here, and touches nothing on its way out.** Two
    // navigations inside one save round trip — Back, Back — leave the first
    // open still flushing after the second has finished; carrying on, it raised
    // `docLoading` over the page the second had drawn and returned at the check
    // after its fetch without lowering it, so the page read "reading …" until
    // the reviewer moved again. Its refusal below would have written its error
    // over that page the same way. The newer open owns the page, and flushes
    // whatever is left itself. Found by the review of HANDOFF-088; older than it.
    if (!current()) return

    // **What the save could not write, this will not discard.** The rule the
    // toolbar's Re-extract already follows, applied to a navigation: this page
    // holds the only copy, so the act stops instead.
    //
    // It refuses by **not leaving**, and writes no address. Putting one back was
    // designed and measured: `location.hash = old` pushes an entry per refusal,
    // so every Back press is answered by a forward push; `replaceState` rewrites
    // the entry the reviewer just arrived at, so the next Back lands on an
    // identical URL, fires no `hashchange` at all, and the press after that
    // escapes past the guard and overwrites *that* entry too — a reviewer
    // walking backwards eats their own history. Both also write the address from
    // an effect, which `router.ts` forbids in as many words.
    //
    // Opening the document the words belong to is therefore never refused —
    // nothing is being left, so nothing can be lost — which is what keeps this
    // from being a trap rather than an escape hatch bolted onto one. Nor are
    // stranded words a reason to stay: a re-parse has made them unwritable
    // anywhere, so staying would keep the reviewer for nothing. They go, and the
    // clear below names them.
    const held = writable ? unwritten() : []
    if (held.length && leaving && !same(leaving, want)) {
      // `docError` is the field `App.tsx` already renders as "here is the
      // document you asked for, and why you do not have it". That is exactly
      // what this is. **It says why per id**, because the three reasons have
      // three different remedies and only one of them is a failure.
      const waiting = held.filter(id => drafts.conflict(id))
      const rest = held.filter(id => !drafts.conflict(id))
      const parts: string[] = []
      if (rest.length && lapFailed) {
        parts.push(
          `${names(rest)} could not be written to ${leaving.src}, and this page holds the ` +
          `only copy of that wording — the log says why. Open ${leaving.src} again to get ` +
          `back to it, or copy the wording out and empty the field.`)
      } else if (rest.length) {
        parts.push(
          `${names(rest)} ${rest.length === 1 ? 'was' : 'were'} typed into while ` +
          `${leaving.src} was being written, so the newest wording is not written yet — ` +
          `nothing failed. Open ${leaving.src} again, which writes it.`)
      }
      if (waiting.length) {
        parts.push(
          `${names(waiting)} changed in ${leaving.src} underneath your edit, so your wording ` +
          `was not written over what is stored now. Open ${leaving.src} again: ` +
          `${waiting.length === 1 ? 'its row shows' : 'each row shows'} both, and you choose ` +
          `which stays.`)
      }
      set({ docError: parts.join(' ') })
      return
    }

    set({ docLoading: true })
    try {
      const doc = await api.getDoc(want)
      // Someone may have opened another document — or this one again — while
      // this was in flight.
      if (!current()) return
      // **Discarded here, and not a statement earlier.** `SegmentRow` reads
      // `drafts.get(seg.id)` by id alone and ids restart at `s0001` in every
      // document, so what the clear prevents is one document's words drawn in
      // another's rows — which becomes possible exactly when the rows change. A
      // fetch that failed leaves the old document on screen, where the entries
      // still mean what they said.
      if (!same(leaving, want) || drafts.stranded()) {
        // Only a re-parse reaches this holding anything: the refusal above has
        // already stopped a document change that would have cost wording.
        const gone = drafts.stranded() ? unwritten() : []
        drafts.clear()
        if (gone.length) {
          get().say(
            `  ${names(gone)}: the re-parse renumbered the segments, so these unsaved ` +
            `edits could not be written anywhere and are gone`,
            'bad',
          )
        }
      }
      set(same(leaving, want) ? { doc, docLoading: false } : { doc, docLoading: false, notice: '' })
    } catch (e) {
      if (!current()) return
      // A refusal the server *answered* — never a request that did not arrive.
      // A file this page has never read may have state after all, and a
      // transport failure says nothing either way; offering an extract over it
      // is offering an unconfirmed re-extract of whatever the server holds.
      const answered = e instanceof ApiError
      set({ docLoading: false, docError: reason(e), readFailed: answered })
      // Not logged when the server answered a read of a file the project lists
      // as not yet extracted: every read of one fails that way, the screen that
      // draws this error offers the extract, and a line in red for following a
      // link in the rail is noise that teaches a reviewer to stop reading red
      // lines. A failure the server did not answer is logged whatever the file.
      if (!(answered && notExtracted(get().state, want))) get().say(reason(e), 'bad')
    }
  },

  refresh: async () => {
    const where = get().shown()
    if (!where) return false
    try {
      const doc = await api.getDoc(where)
      // **Only while the ledger is still showing the document this refresh was
      // about** — compared by value, because `open()` mints a fresh address on
      // every call. It used to accept the reply when `at` named the document
      // too, and that let it put a document on screen that `open()` had
      // declined to show: `at` names a chapter the moment a navigation asks for
      // it, `doc` stays on the chapter being left while that one's words cannot
      // be written, and a refresh of the first chapter still in flight then
      // swapped `doc` underneath them. The next save posted the second
      // chapter's words onto the first chapter's ids with the first chapter's
      // tokens, and a re-extract stranded them as "renumbered" (found by the
      // review of HANDOFF-088; older than it). A document the page is on its
      // way to is `open()`'s to read.
      //
      //
      // What it does not do is order itself against the page's other reads — a
      // refresh asked before a save and answered after the save's own re-read
      // still puts the older state back. Four ways of ordering them were built
      // and reviewed in HANDOFF-088, and each left an ordering uncovered; that is
      // HANDOFF-096's to design, and its cases are `it.fails` in the suite.
      if (!same(get().shown(), where)) return false
      set({ doc })
      return true
    } catch (e) {
      get().say(reason(e), 'bad')
      return false
    }
  },

  /**
   * Flush every dirty row. Returns false when something was held back or
   * refused, so a caller about to do something destructive can stop.
   */
  save: () => flush(get).then(o => o.ok),

  writeOut: () => flush(get).then(o => left(o.failed)),

  decline: why => stopped(get, why),

  /**
   * Start a run and follow it to a terminal state.
   *
   * `ids` names segments and **outranks everything else in the selection** — the
   * mode table, the hold exclusion and the pre-filter that drops segments a
   * person wrote. `mode` still chooses the routing stage and the prompt, so it
   * is sent either way.
   */
  runJob: async (mode, ids, overwriteHuman, note) => {
    if (get().running) return
    // Said, because the one caller that can reach this unsettled is a row whose
    // dialog was answered while the page read the document again underneath
    // it, and a confirmed act that vanishes without a line is indistinguishable
    // from one never made (sixth review).
    if (!get().settled()) {
      get().say(
        `  ${ids ? ids.join(', ') : mode} not sent: the page was between documents, or reading ` +
        `one, when this was confirmed — press it again once the document is on screen`,
        'warn',
      )
      return
    }
    const where = get().shown()
    if (!where) return

    // **Raised before the first await.** The `running` test above is only a
    // guard if nothing can pass it twice, and `save()` below is a full round
    // trip whenever anything is dirty — with every run control still enabled.
    // Two clicks in that window started two runs, and two `llm:*` writes to one
    // segment are last-write-wins with no token and no check.
    set({
      running: true,
      runName: ids ? `the ${mode} run on ${names(ids)}` : `the ${mode} run over ${where.src}`,
    })

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

    // What the run's own end leaves beside the bar. A run that ends clears the
    // notice, because "let the run finish" is no longer true then — except the
    // one this run says about itself when it declines to start, and one about
    // unwritten wording that is still true (`keeping`), which a run ending does
    // nothing to.
    let keep: Declined | null = null
    try {
      // **Not started over wording only this page holds.** The server chooses a
      // run's segments from what it has stored, and origin precedence can only
      // protect a person's wording it has been given: a row whose save failed is
      // still pending there, so Translate pending would bill a draft of a
      // sentence the reviewer has already translated, which their next save then
      // writes over.
      const why = declines(
        await get().writeOut(), `The ${mode} run was not started`, where.src,
        'a run chooses its segments from what is stored, so it could replace wording that exists only on this page',
      )
      if (why) {
        get().say('  ' + why.text, 'warn')
        keep = why
        return
      }
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
      remember({ id: job.id, ...where })
      await follow(set, get, job.id, where)
    } catch (e) {
      get().say('  ' + reason(e), 'bad')
    } finally {
      forgetJob()
      set({ running: false, runName: '' })
      if (keep) sayBeside(get, keep)
      else endOfRun(get)
    }
  },

  resume: async () => {
    const held = remembered()
    if (!held || get().running) return
    set({ running: true, runName: `the run ${held.id} on ${held.src}, started before this page was loaded` })
    get().say(`— following ${held.id}, started before this page was loaded —`, 'warn', true)
    try {
      await follow(set, get, held.id, { src: held.src, lang: held.lang })
    } catch (e) {
      get().say('  ' + reason(e), 'bad')
    } finally {
      forgetJob()
      set({ running: false, runName: '' })
      endOfRun(get)
    }
  },

  abandon: () => {
    // **Nothing is cancelled.** There is no endpoint that could, and the run's
    // thread keeps writing whatever this page does — so the honest control is
    // "stop following", and it says exactly that. Without it a backend that
    // blocks forever (a llama.cpp router loading a model does not answer 503, it
    // waits) leaves every run control on this page disabled with no way out but
    // restarting `lx web`.
    if (!get().running) return
    abandoned = true
    forgetJob()
    set({ running: false, runName: '' })
    endOfRun(get)
    get().say(
      '  stopped following this run. It has not been cancelled — there is no way to ' +
      'cancel one — so the server is still writing, and what it writes will be there ' +
      'when you re-open the document.',
      'warn',
    )
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
          `  ${names(r.stale)}: the wording changed while this was in flight, ` +
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
    // A check reports on what the server holds and persists that report, so a
    // check over a failed save is a verdict on wording the reviewer has already
    // replaced — at `good`, possibly, over a row they are still fixing. A row
    // waiting on their choice does not stop it: what is stored there is what a
    // check is about until they choose.
    if (stopped(get, declines(
      await get().writeOut(), 'Nothing was checked', where.src,
      'a check reports on what is stored, without it',
    ))) return
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

  extract: where => reExtract(set, get, where, null),
  startOver: (where, register) => reExtract(set, get, where, register),

  extractUntracked: async where => {
    const key = JSON.stringify([where.src, where.lang])
    if (get().running || asking.has(key)) return false
    asking.add(key)
    try {
      if (!await get().reloadState()) return false
    } finally {
      asking.delete(key)
    }
    if (!notExtracted(get().state, where)) {
      // Extracted elsewhere since this page read it, most likely — or no longer
      // matched by `sources`. Either way there is nothing to extract, and the
      // document is `open()`'s to read, where the address still names it.
      const here = same(get().at, where)
      get().say(
        `  ${where.src} [${where.lang}] is no longer listed as not extracted, so nothing was ` +
        `extracted — it has been extracted elsewhere, no longer matches \`sources\`, or now ` +
        `shares its identity with another path` +
        (here ? '; reading it again' : '; open it from the rail'),
        'warn',
      )
      if (here) await get().open(where.src, where.lang)
      return false
    }
    // Asked again after the round trip: a run, or the extract of another file
    // clicked meanwhile, may have started. Said, because a click that does
    // nothing and says nothing is indistinguishable from one never made. After
    // the listing test and not before it, because a file that no longer needs
    // extracting needs nothing to wait for.
    if (get().running) {
      get().say(
        `  ${where.src} was not extracted: something else started running while this was ` +
        `asking — try again when it finishes`,
        'warn',
      )
      return false
    }
    get().say(`— extract ${where.src} [${where.lang}] —`, 'plain', true)
    return get().extract(where)
  },

  render: async () => {
    const where = get().shown()
    if (!where) return
    // The file stands for the document, so it waits for the document to be
    // decided: not over wording that failed to reach the server, and not over a
    // row whose reviewer has yet to choose between theirs and what is stored.
    if (stopped(get, declines(
      await get().writeOut(), 'No file was written', where.src,
      'the file would be written without it', true,
    ))) return
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
    // The memory is tracked and `store.load_tm` keeps the last record per key,
    // so what is banked here is what every other document is offered. Banking
    // the stored wording of a row whose reviewer's own failed to arrive, or who
    // has yet to choose, files the wording they are replacing as the answer.
    if (stopped(get, declines(
      await get().writeOut(), 'Nothing was banked', where.src,
      'the memory would bank what is stored there instead', true,
    ))) return
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
          `  ${names(r.stranded)}: this wording speaks a numbering the document has ` +
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

// A choice in a row (*Discard mine*, *Write mine over it*), a cleared map and a
// settled save all move the registry, and each can make a notice about
// unwritten wording false. The registry announces only crossings, so this costs
// nothing per keystroke.
drafts.subscribe(() => { recheck(useStore.getState) })

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

/**
 * Whether the project lists this document as not yet extracted.
 *
 * `GET /api/state`'s `untracked`, which is a **snapshot**: this page reads it at
 * startup and after its own extracts, and a terminal can extract a file in
 * between. So it is never the only fact an act rests on — the page that offers
 * an extract asks it only after a read of the document has just failed, which
 * is the fresh half. One predicate, read by `open()` and by that page, so the
 * two cannot disagree about which files it covers.
 */
export function notExtracted(state: StateResponse | null, where: DocAddress): boolean {
  return !!state?.untracked.some(u => u.source === where.src && u.lang === where.lang)
}

/** `ApiError` when the server refused, so a caller can branch on the status.
 *  `403` means never writable whatever the value; `400` means fix the payload
 *  and send it again. The sentence is for a person and is never parsed. */
export const statusOf = (e: unknown): number | null =>
  e instanceof ApiError ? e.status : null

/**
 * One save on the wire at a time; see `onWire`.
 *
 * **The lock covers the request and the settling of its reply, and not the
 * re-read after it.** A re-read is a whole `GET /api/doc`, and the *request* of
 * an act queued behind it — Check, a run, a flush on the way out — would wait on
 * a read that concerns none of what it sends.
 *
 * **But no caller gets its answer before the last round's re-read has landed**,
 * its own round's or the one before it. A caller reads the document right after
 * `save()` answers: "Draft again" decides from the segment's origin whether to
 * ask about replacing a person's wording. A queued caller whose own round sent
 * nothing used to answer as soon as the lock was released, before the previous
 * round's re-read — the origin was the one from before the save, no question was
 * asked, the model was billed and the write refused (found by the review of
 * HANDOFF-091). The same held for a call made after the lock was released and
 * before that re-read landed. `settling` is that re-read, per drafts epoch: a
 * re-read of a document replaced since is not something this one waits on.
 */
let settling: Promise<void> | null = null
let settlingEpoch = -1

function flush(get: () => Store): Promise<Outcome> {
  const epoch = drafts.epoch()
  if (!onWire || onWireEpoch !== epoch) {
    // Started synchronously when nothing is on the wire, so the write epoch
    // moves before the caller's next statement, as it always has.
    const sent = round(get)
    const lock: Promise<void> = sent.then(() => undefined, () => undefined)
    onWire = lock
    onWireEpoch = epoch
    queued = null
    void lock.then(() => { if (onWire === lock) onWire = null })
    const prior = settlingEpoch === epoch ? settling : null
    const whole = sent.then(async ({ outcome, reread, landing }) => {
      // The ids this round sent stop counting as in flight only once the row
      // shows what was written — and only in the parse they were sent in.
      const end = (): void => { if (drafts.epoch() === epoch) drafts.landed(landing) }
      if (reread) {
        const read = get().refresh()
        if (!await within(read, SAVE_WAIT_MS)) {
          // **The caller is answered; the in-flight count is not ended.** The row
          // still shows the wording from before the save, and a field typed back
          // to it would otherwise drop the entry while the server holds the newer
          // words (found by the third review of HANDOFF-091). It ends when the
          // read does, whenever that is.
          void read.finally(end)
          get().say(
            `  the document was not read back within ${SAVE_WAIT_MS / 1000} seconds of this save, ` +
            `so what is on screen may be older than what was written`,
            'warn',
          )
          return outcome
        }
      }
      end()
      return outcome
    })
    const answer = prior ? Promise.all([whole, prior]).then(([outcome]) => outcome) : whole
    // Chained through `prior`, not this round's alone: a round that sends
    // nothing finishes at once, and recording only that would let the next
    // caller answer before the earlier round's re-read.
    const done: Promise<void> = answer.then(() => undefined, () => undefined)
    settling = done
    settlingEpoch = epoch
    void done.then(() => { if (settling === done) settling = null })
    return answer
  }
  if (!queued) {
    const after = onWire
    const next: Promise<Outcome> = after.then(() => {
      if (queued === next) queued = null
      return flush(get)
    })
    queued = next
  }
  return queued
}

/**
 * How long a save may go unanswered before this page stops waiting for it.
 *
 * `api.ts` sets no timeout of its own, and one save on the wire at a time made a
 * request that never answers a lock: no later save was sent, and `open()` —
 * which waits on its flush — never left the document (found by the review of
 * HANDOFF-091; before it, a second save went out beside the hung one). Past this
 * the request is abandoned and treated as failed: the words stay in `drafts`,
 * the lock is released, and the next save sends them again. If the server did
 * write them after all, that next save is a compare-and-swap against the token
 * from before, so it comes back as a conflict carrying what was stored — the
 * reviewer is shown it, and nothing is overwritten silently.
 *
 * Thirty seconds is six times the server's own SQLite busy timeout
 * (`store.BUSY_TIMEOUT`, five), so a save waiting its turn behind a run's batch
 * write is not cut off.
 */
export const SAVE_WAIT_MS = 30_000

/**
 * Whether `p` settled within `ms`. The re-read after a save is bounded like the
 * save itself: no `save()` answers before the last round's re-read has landed,
 * so a `GET /api/doc` that never answers would otherwise hold every later save
 * and every `open()` for good — the lock `SAVE_WAIT_MS` removed from the POST,
 * arriving one request later (found by the second review of HANDOFF-091). Past
 * the bound the caller is answered and the read is left to land, or not.
 */
function within(p: Promise<unknown>, ms: number): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const late = new Promise<boolean>(resolve => { timer = setTimeout(() => { resolve(false) }, ms) })
  return Promise.race([p.then(() => true, () => true), late]).finally(() => { clearTimeout(timer) })
}

/**
 * Say beside the bar that a round wrote nothing — only while the parse it was
 * sent from is still the one on screen: ids restart at `s0001` in every
 * document, so naming them over another document's rows would point at
 * paragraphs nobody typed into. That parse's own words have gone with it, and
 * the log has the line.
 */
function notWritten(get: () => Store, ids: string[], epoch: number): void {
  if (drafts.epoch() !== epoch) return
  const text =
    `${names(ids)} ${ids.length === 1 ? 'was' : 'were'} not written — the save failed, and the ` +
    `log says why. The wording is still in ${rows(ids)}, marked unsaved, and the next save sends ` +
    `it again: leaving the field, Ctrl+Enter, or Check and the other acts that save first.`
  sayBeside(get, { text, ids, why: 'failed' })
}

/** One round of writing: what `save()` was before HANDOFF-091, and its reply.
 *  `landing` is what it sent, which `flush()` ends once it has been read back. */
async function round(get: () => Store): Promise<{ outcome: Outcome; reread: boolean; landing: string[] }> {
  {
    // A field emptied since the notice went up changes no count the registry
    // announces, so it is asked here too: every save passes this point.
    recheck(get)
    // **Nothing stranded is written, by anyone.** A re-extract this page asked
    // for has renumbered the ids every entry is keyed on, and until the new
    // parse is on screen `doc` still carries the old one's tokens — so a blur in
    // that window would post a sentence onto whatever paragraph now holds the
    // id. The mark is checked here because every write goes through here: it
    // was first checked only in `open()`, and a blur during the re-extract's own
    // reload walked straight past it (found by an adversarial pass, 2026-09-21).
    // `false`, because something is being held back; `open()` names what goes.
    if (drafts.stranded()) return { outcome: { ok: false, failed: false }, reread: false, landing: [] }
    const where = get().shown()
    if (!where || !drafts.size()) return { outcome: { ok: true, failed: false }, reread: false, landing: [] }
    const doc = get().doc
    if (!doc) return { outcome: { ok: true, failed: false }, reread: false, landing: [] }

    // Blank edits are held back rather than sent. The server refuses an empty
    // target for the WHOLE request, and this is one map for the whole ledger —
    // so one cleared segment would refuse every later edit in the same session,
    // over and over, because the poisoned batch keeps being resent. Held back,
    // the good edits go through and the blank one is named once.
    //
    // A wording waiting on the reviewer's choice is held back too, silently — its
    // row says so, and it is not sent until the reviewer chooses (HANDOFF-091).
    const held: string[] = []
    const waiting: string[] = []
    const targets: Record<string, string> = {}
    for (const [id, text] of drafts.entries()) {
      if (drafts.conflict(id)) waiting.push(id)
      else if (wording(text)) targets[id] = text
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
    if (!ids.length) return { outcome: { ok: !held.length && !waiting.length, failed: false }, reread: false, landing: [] }

    // The token each edit was based on, so a translation batch that landed
    // mid-review — or a second window — cannot overwrite a sentence silently.
    // Per id and optional: a segment we hold no token for is written the way it
    // always was. An entry may carry its own: the token of the wording a
    // reviewer chose to write over, or of this page's own write that the entry
    // was typed on top of.
    //
    // *Known gap, not closed here:* for every other entry the token is read from
    // `doc` when the save is sent, not when the edit began, so a re-read landing
    // between the first keystroke and the save moves the base past wording the
    // reviewer never saw. That needs the token of the page's own writes in
    // general, which is HANDOFF-096's to design; it is an `it.fails` there.
    const base: Record<string, string> = {}
    const byId = new Map(doc.segments.map(s => [s.id, s]))
    for (const id of ids) {
      const token = drafts.base(id) ?? byId.get(id)?.token
      if (token) base[id] = token
    }

    const epoch = drafts.epoch()
    writes += 1
    drafts.sending(ids)
    let r: SaveResponse
    const abandon = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const unanswered = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        abandon.abort()
        reject(new Error(
          `the server did not answer this save within ${SAVE_WAIT_MS / 1000} seconds, so this ` +
          `page stopped waiting and took nothing as written — ${names(ids)} stay unsaved here ` +
          `and go out again with the next save`,
        ))
      }, SAVE_WAIT_MS)
    })
    try {
      r = await Promise.race([api.postSave({ ...where, targets, base }, abandon.signal), unanswered])
    } catch (e) {
      writes += 1
      // Kept on failure: an empty target is a 400, and clearing first would
      // throw away every other edit in the batch along with the one the server
      // would not take.
      get().say('  ' + reason(e), 'bad')
      notWritten(get, ids, epoch)
      return { outcome: { ok: false, failed: true }, reread: false, landing: ids }
    } finally {
      clearTimeout(timer)
    }
    writes += 1

    // **A reply about a parse no longer on screen settles nothing here.** Ids
    // restart at `s0001` in every document, so settling it would forget, keep
    // or mark some other paragraph's entry — and that includes what is in
    // flight, which is why `flush()` and not this function ends the round in
    // `drafts`, and only in the epoch it was sent in.
    if (drafts.epoch() !== epoch) return { outcome: { ok: false, failed: false }, reread: false, landing: ids }

    // **A reply this page cannot read takes nothing as written.** It used to
    // forget every entry it sent and only then fail on `r.conflicts`.
    if (!r || typeof r.conflicts !== 'object' || r.conflicts === null ||
        typeof r.stored !== 'object' || r.stored === null || !Array.isArray(r.unknown)) {
      get().say(
        `  the server answered this save with something this page cannot read, so nothing ` +
        `was taken as written — ${names(ids)} stay unsaved here`,
        'bad',
      )
      notWritten(get, ids, epoch)
      return { outcome: { ok: false, failed: true }, reread: false, landing: ids }
    }
    // A readable answer: a notice that wording had failed, or was not yet
    // written, is about what this round has just sent or found waiting — so it
    // is no longer the state of things. What this round could not write, it
    // says below. A notice about a row waiting on the reviewer's choice is not
    // answered by a save at all, only by the choice (`recheck`).
    if (said && (said.why === 'failed' || said.why === 'newer') && keeping(get)) get().setNotice('')
    if (said && said.why !== 'waiting') said = null

    // Every id sent is exactly one of: named no segment, refused, written. A
    // conflict whose stored text is the text sent is a write, not a refusal —
    // with one save on the wire this page no longer sends itself into one, so
    // it is a backstop, and normalization means it is not the mechanism.
    const unknown = new Set(r.unknown)
    const written: Record<string, string> = {}
    const tokens: Record<string, string | undefined> = {}
    const lost: string[] = []
    for (const id of ids) {
      if (unknown.has(id)) continue
      const c = r.conflicts[id]
      if (c && c.text !== targets[id]) { lost.push(id); continue }
      written[id] = targets[id]!
      tokens[id] = (c ?? r.stored[id])?.token
    }
    drafts.settle(written, tokens)
    for (const id of lost) drafts.mark(id, r.conflicts[id]!)
    if (r.unknown.length) {
      drafts.forget(r.unknown)
      get().say(`  ${names(r.unknown)} name no segment and were ignored`, 'warn')
    }
    const refused = lost.filter(id => drafts.conflict(id))
    if (refused.length) {
      get().say(
        `  ${names(refused)} changed underneath this edit and ` +
        `${refused.length === 1 ? 'was' : 'were'} not written — your ` +
        `wording is still in ${refused.length === 1 ? 'its row' : 'each row'}, beside what is ` +
        `stored now, and nothing sends it until you choose there which stays`,
        'bad',
      )
    }
    // **Re-read only the document this page is still on.** A flush that ran
    // because the address moved is followed by the arriving document's own
    // fetch, so re-reading the one being left is a second full `GET /api/doc`
    // — every segment of it — thrown away under the loading screen. Decided
    // from state rather than from an argument the caller passes: a re-read a
    // caller can turn off is two spellings of one rule, and the reviewer who
    // goes straight back re-reads it in that fetch anyway.
    return {
      outcome: { ok: !held.length && !waiting.length && !refused.length, failed: false },
      reread: same(get().at, where),
      landing: ids,
    }
  }
}

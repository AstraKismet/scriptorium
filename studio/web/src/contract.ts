/**
 * The workbench HTTP contract, as TypeScript.
 *
 * `docs/contracts/workbench-http.md` is authoritative and this file is its
 * translation — nothing here is a design decision of its own. Where the two
 * disagree the document wins and this file is the bug.
 *
 * Three things keep them from drifting, and they chain:
 *
 *   1. `RESPONSE_KEYS` at the bottom names each endpoint's top-level reply keys,
 *      and a block of type-level assertions beside it fails `tsc` if any array
 *      and its interface stop agreeing — in **both** directions, so neither a
 *      forgotten key nor an invented one survives a typecheck.
 *   2. `tests/test_studio_contract.py` parses `RESPONSE_KEYS` out of this file
 *      and compares it against the tables in the contract document, endpoint by
 *      endpoint. That test runs in the Python suite, which is what CI runs, so
 *      the guard does not depend on anybody remembering to run `npm`.
 *   3. `tests/test_contract.py` already compares the document against a live
 *      reply.
 *
 * So: interface ≡ array ≡ document ≡ server. Break any link and something red
 * says which.
 *
 * No field is optional here unless the document says a key can be absent. The
 * contract's own rule is that a key is "present and empty" rather than omitted —
 * `collisions`, `kept`, `ambiguous`, `replaced`, `waived_source`, `stale`,
 * `refused`, `usage` — precisely so a client never has to tell "none" from "an
 * older server", and modelling one of those as optional would put a branch back
 * that the server exists to remove.
 */

/**
 * The contract version this client is written against.
 *
 * The frontend reads `contract_version` from `GET /api/state` at startup and
 * refuses to run against a number it does not know. That refusal is the entire
 * reason the field exists — see the document's *Versioning* section.
 */
export const CONTRACT_VERSION = 4

// ── shared shapes ──────────────────────────────────────────────────────────

/**
 * A validation finding. `rule` is deliberately `string` and not a union: the
 * document says the rule set is expected to grow, that a new name is additive,
 * and that a consumer must not treat the list as closed. `severity` is the same
 * — a hand-edited `config/glossary.csv` can put any string in column four, and
 * anything that is not exactly `error` counts as a warning.
 */
export interface Issue {
  seg: string
  rule: string
  severity: string
  /** Human-readable, for a person. Not stable — never parse it. */
  message: string
}

/** The rule names this build knows about, for grouping and colour only. */
export const KNOWN_RULES = [
  'bare_term', 'containment', 'dnt', 'eol', 'escaping', 'glossary', 'held',
  'length', 'lexicon', 'missing', 'numbering', 'numbers', 'punct', 'spacing',
  'tags', 'untranslated', 'waived',
] as const

/** `error` fails the build; everything else is a warning, whatever it says. */
export const isError = (issue: Issue): boolean => issue.severity === 'error'

/** A segment's block kind. Plain text emits only `para` and `heading`. */
export type Kind = 'para' | 'heading' | 'list' | 'quote' | 'cell'

/**
 * Where a target came from. `llm:<mode>` carries whatever mode the request
 * sent, so this is a string with three known literal members rather than a
 * closed union.
 */
export type Origin = 'human' | 'agent' | 'carryover' | 'tm' | 'tm:legacy' | (string & {})

/** An element of `GET /api/doc`'s `segments`. */
export interface Segment {
  /** `s0001`, per document, sequential — and **reassigned on every parse**. */
  id: string
  kind: Kind
  /** Derived from the target text, on the way in and on the way out. */
  status: 'pending' | 'translated'
  origin: Origin | null
  /** The **masked** text — placeholders as `⟦n⟧`, not the raw source. */
  source: string
  /** `""` when absent, never `null`. */
  target: string
  /** A closed vocabulary. `held` means no queue that selects work takes it. */
  review: 'held' | null
  /** Independent of `review`; a segment may be both held and waived. */
  waived: boolean
  /** Opaque. Store it, hand it back as `base`; never compute or compare it. */
  token: string
  /** Only this segment's. */
  issues: Issue[]
}

/**
 * An element of `GET /api/preview`'s `blocks`.
 *
 * `id === null` **is** the discriminator for a run of skeleton — there is no
 * `type` tag — and it is spelled the same way a segment's `id` is, so the two
 * join on it.
 */
export interface Block {
  id: string | null
  kind: Kind | null
  /**
   * Which branch produced `text`. Since version 4 a stored translation does not
   * always take `target`: a wording whose placeholders cannot be substituted
   * without malforming the document answers `source` or `marker` like a segment
   * nobody wrote. Do not render "untranslated" from this field alone — join on
   * `id` and read `issues`.
   */
  from: 'target' | 'source' | 'marker' | null
  /** May be empty. `blocks.map(b => b.text).join('')` is `text`, byte for byte. */
  text: string
}

/** An element of `GET /api/state`'s `providers`. */
export interface Provider {
  name: string
  /** Echoed from configuration without validation; `build()` refuses it later. */
  kind: string
  /** The provider's own default model. */
  model: string
  /** **Printable form** — userinfo stripped, query replaced. Not the URL to call. */
  base_url: string
  needs_key: boolean
  /** Whether that variable is set **in the server process's** environment. */
  key_present: boolean
  /** The variable's name. Never its value. */
  key_env: string
  /** `null` means the key is absent from the spec, so the transport default
   *  applies (120 s, 0.2, 4096, 3). Render a blank, never the default — writing
   *  it into a box lets Save pin an inherited value. */
  timeout: number | null
  temperature: number | null
  max_tokens: number | null
  retries: number | null
  /** Present **only** when this provider's block cannot be read. A row carrying
   *  it is not configured, whatever the other eleven keys hold. */
  error?: string
}

/** A value of `GET /api/state`'s `routing`, and of `POST /api/config`'s. */
export interface RoutingStage {
  provider: string
  model: string
  /** Present only when the configured entry is malformed. */
  error?: string
}

/** The three stages, and the one list of them. */
export const ROUTING_STAGES = ['draft', 'polish', 'repair'] as const
export type Stage = (typeof ROUTING_STAGES)[number]

export type Routing = Record<string, RoutingStage>

/** An element of `GET /api/models`'s `models`. */
export interface Model {
  id: string
  /** `""` unless the backend volunteers one. llama.cpp's router reports
   *  `unloaded` / `loading` / `sleeping` / `loaded`. */
  status: string
}

/** An element of `GET /api/state`'s `docs`. */
export interface DocRow {
  source: string
  lang: string
  total: number
  /** Segments with a **non-empty** target. */
  done: number
}

/** An element of `GET /api/state`'s `untracked`. */
export interface UntrackedRow {
  source: string
  lang: string
}

/**
 * An element of `GET /api/state`'s `collisions`.
 *
 * `offered` is `null` for **two** different reasons — a tracked document already
 * holds the identity, *or* no target language is configured to offer anything
 * under. Do not read it as "already tracked".
 */
export interface Collision {
  paths: string[]
  offered: string | null
}

/** The narrowed report `GET /api/doc` carries. Not `POST /api/check`'s shape. */
export interface DocReport {
  segments: number
  translated: number
  errors: number
  warnings: number
  by_rule: Record<string, number>
}

// ── requests ───────────────────────────────────────────────────────────────

/** Every endpoint but `/api/state`, `/api/models`, `/api/sentences` and
 *  `/api/config` addresses one document this way. */
export interface DocAddress {
  src: string
  lang: string
}

export interface ExtractRequest extends DocAddress {
  /**
   * ⚠️ **A genuine JSON boolean, always.** The server type-checks neither this
   * nor `tone` — divergence (28), open — and evaluates this for truthiness in
   * Python, so the *string* `"false"` is a reset that discards the document's
   * translations. There is no server-side guard; the client is the guard.
   */
  reset?: boolean
  /**
   * Required, non-blank, whenever `reset` is true — the whole of version 3. A
   * reset reads no prior row, so it has no register to keep, and until version 3
   * it refroze silently to the configured default.
   *
   * A client offering "start over" must **ask a person which register**. The
   * instruction to forward `GET /api/doc`'s `tone` was *withdrawn*: nothing
   * validates a register value, so a client that guesses is handed the wrong one
   * rather than refused.
   */
  tone?: string
}

export interface SaveRequest extends DocAddress {
  targets: Record<string, string>
  /** Per id, and optional. An id present here is written only if the stored
   *  target still hashes to this value; an id absent from it is written
   *  unconditionally. Sending nothing here is last-write-wins. */
  base?: Record<string, string>
}

export interface HoldRequest extends DocAddress {
  ids: string[]
  /** `false` lifts the hold. */
  held: boolean
}

export interface WaiveRequest extends DocAddress {
  ids: string[]
  /** `false` lifts the waiver. A `null` would read as `false` and lift one. */
  waived: boolean
}

export type Mode = 'draft' | 'polish' | 'repair'

export interface TranslateRequest extends DocAddress {
  mode: Mode
  /** When present and non-empty this **overrides `mode`'s selection entirely** —
   *  the mode table, the hold exclusion and the origin pre-filter alike. `mode`
   *  still names the routing stage, so send it either way. */
  ids?: string[]
  provider?: string
  model?: string
  /** The most segments this run may send. `0` and absent mean the whole
   *  selection. **Not applied beside `ids`** — but still checked, so a malformed
   *  value is refused on a request that would have ignored it. */
  limit?: number
  /** Let this run replace segments a person wrote. Never default it on. */
  overwrite_human?: boolean
}

export interface RenderRequest extends DocAddress {
  /** `""` means "use the default output path". */
  out?: string
  fallback?: boolean
}

export interface SentencesRequest {
  texts: string[]
}

export interface StyleRequest extends DocAddress {
  /** Default is the whole document. **Selection is against the whole set,
   *  because a batch is a scene** — asking one segment at a time loses exactly
   *  the dialogue the feature exists for. */
  ids?: string[]
}

export interface SuggestRequest extends DocAddress {
  ids?: string[]
  /** Greater than 0 and at most 1. Default `0.7`. */
  cutoff?: number
  /** How many **segments** to answer. Default `5`; `0` means every segment. */
  limit?: number
  /** Suggestions **per segment**. Default `5`; `0` means every match. */
  most?: number
}

export interface ConfigRequest {
  key: string
  /** Presence is what counts — a JSON `null` **is** a value. Leaving the field
   *  out on a write is a `400`. */
  value?: unknown
  unset?: boolean
  /** Required to write **or remove** a `providers.*.base_url`. */
  confirm_base_url?: boolean
}

export interface JobRequest {
  id: string
}

// ── responses ──────────────────────────────────────────────────────────────

export interface StateResponse {
  contract_version: number
  /** The **package** version. Not the contract version; never use it as one. */
  version: string
  /** A label to show a person, never an input to a path comparison. */
  cwd: string
  targets: string[]
  providers: Provider[]
  routing: Routing
  docs: DocRow[]
  untracked: UntrackedRow[]
  collisions: Collision[]
}

export interface DocResponse {
  source: string
  lang: string
  /** The register frozen onto the document at extract. */
  tone: string
  report: DocReport
  segments: Segment[]
}

export interface PreviewResponse {
  text: string
  blocks: Block[]
  /** A **count**, not a list: segments with no *usable* target. The list form is
   *  `blocks` — exactly those whose `from` is not `"target"`. */
  missing: number
  default_out: string
}

export interface ModelsResponse {
  /** Echoes the `provider` you sent even when no such backend is configured. */
  provider: string
  /** The model id this project would send today. Present on the failure path
   *  too, and **that is what it is for**. */
  configured: string
  /** Present and empty when the listing failed. Advisory — it gates nothing. */
  models: Model[]
  /** Always present. **This endpoint answers `200` whatever happens**, so this
   *  field and not the status is the failure signal. */
  error: string | null
}

export interface SentencesResponse {
  /** Parallel to `texts` by index, and `sentences[i].join('')` is `texts[i]`
   *  exactly. Walk the string with a cursor — two sentences in one paragraph may
   *  be byte-identical, and searching would put both highlights on the first. */
  sentences: string[][]
}

export interface VoiceNote {
  names: string[]
  notes: string
}

export interface StyleResponse {
  source: string
  lang: string
  tone: string | null
  /** What `ids` resolved to, in document order, so a client can see that an id
   *  matched nothing. */
  ids: string[]
  /** The always-on half, exactly as it reaches a request. */
  voice: string
  voice_notes: VoiceNote[]
}

export interface Suggestion {
  /** A `difflib` ratio, 0 to 1, four places. **Not a standard anybody else
   *  implements** — read `algorithm` and never compare across a change in it. */
  score: number
  source: string
  target: string
  context: string | null
  tone: string | null
  variant: string | null
  /** Whether the wording was banked from a segment a reviewer had waived. */
  waived: boolean
}

export interface SuggestSegment {
  id: string
  /** The **raw** source text the comparison was made on. */
  source: string
  examined: number
  /** ⚠️ A work budget stopped the search before the memory ran out: there may be
   *  better matches that were never compared. A panel that quietly stopped
   *  looking is indistinguishable from a memory that holds nothing. */
  truncated: boolean
  suggestions: Suggestion[]
}

export interface SuggestResponse {
  source: string
  lang: string
  tone: string | null
  algorithm: string
  cutoff: number
  records: number
  segments: SuggestSegment[]
}

export interface ExtractResponse {
  segments: number
  reused: number
  /** Counts **segments**, not refusals: segments where every proposal was
   *  refused. A refusal with an accepted proposal behind it counts in `reused`
   *  and is named in `replaced`. */
  rejected: number
  /** Ids whose stored target the acceptance path refused and this endpoint kept
   *  anyway. They come back `translated` holding wording that fails validation. */
  kept: string[]
  /** Ids the position diff could not establish. **Orthogonal to the other two**
   *  — a segment can appear here *and* in one of them, so rendering the three as
   *  disjoint buckets double-counts. */
  ambiguous: string[]
  /** Ids where a memory hit was accepted over wording the document already held.
   *  Their `origin` is now `tm`, and there is no error to find them by. */
  replaced: string[]
  /** Ids that took a banked wording a reviewer had waived where it was
   *  committed. **The waiver did not travel** — they arrive unwaived. */
  waived_source: string[]
}

export interface StoredTarget {
  /** The text **as stored**, after normalization and reseating. */
  text: string
  token: string
}

export interface SaveResponse {
  /** Always equal to the size of `stored`. */
  applied: number
  /** Ids with no matching segment. Ignored rather than refused. */
  unknown: string[]
  stored: Record<string, StoredTarget>
  /** Ids refused because `base` was stale, with the **current stored** text and
   *  token, so a client has something authoritative to present a merge against. */
  conflicts: Record<string, StoredTarget>
}

export interface HoldResponse {
  applied: number
  unknown: string[]
}

export interface WaiveResponse {
  /** A no-op is not counted, so lifting a waiver nobody placed answers `0`. */
  applied: number
  unknown: string[]
  /** Ids left alone because the stored target moved between this request being
   *  prepared and the write. Present and empty when it did not happen. */
  stale: string[]
}

export interface CheckResponse {
  source: string
  lang: string
  segments: number
  translated: number
  errors: number
  /** Every issue that is not `error`, whatever its severity string. */
  warnings: number
  by_rule: Record<string, number>
  /** **Flat**, not grouped by segment. */
  issues: Issue[]
}

export interface Route {
  provider: string
  model: string
  error?: string
}

export interface TranslateResponse {
  id: string
  /** Segments selected, fixed at creation and **after `limit` has capped them**.
   *  `0` is legal and means the run does nothing. This surface deliberately does
   *  not say how many were left behind. */
  total: number
  /** What this run will actually dispatch to. The only place that answer appears
   *  outside a log line this contract forbids parsing. */
  route: Route
}

/** What a run cost, in tokens. Written **once**, when the run reaches a terminal
 *  state — so a poll on a job that is not `done` reads zeros and must not draw a
 *  cost from them. */
export interface Usage {
  prompt: number
  completion: number
  /** `prompt + completion`, computed by the server and never read from a reply. */
  total: number
  /** Completion responses whose body was parsed. */
  replies: number
  /** How many of those carried a usage object this project could read.
   *  **`total` is a floor, not a cost, unless `reported === replies`.** */
  reported: number
}

/** `[segment_id, reason]`. */
export type Failure = [string, string]

export interface JobRecord {
  id: string
  /** The thread has finished, successfully **or not**. */
  done: boolean
  total: number
  /** Accumulated per batch as they land, and right on the failure path. */
  applied: number
  /** Free text, not stable, **and not to be parsed**. */
  log: string[]
  failures: Failure[]
  /** Segments left alone because a person had written them. Accumulated per
   *  batch, like `applied`. */
  refused: string[]
  /** ⚠️ A non-null `error` does **not** mean nothing was written. */
  error: string | null
  usage: Usage
}

/** An id with no record answers `200` with **one key** — not `404`, not `400`.
 *  Two sentences are possible: a job that existed and was evicted, and one that
 *  never did. Tell the record apart by the absence of `done`, never by the
 *  presence of `error` — a live record carries `error: null` throughout. */
export interface JobUnknown {
  error: string
}

export type JobResponse = JobRecord | JobUnknown

export const isJobRecord = (r: JobResponse): r is JobRecord => 'done' in r

export interface RenderResponse {
  /** The path actually written. */
  wrote: string
  missing: number
}

export interface CommitResponse {
  /** Memory lines that were **new** — not the count of translated segments.
   *  Committing an unchanged document twice answers `0`, and that is correct. */
  committed: number
  /** Not banked because `lx check` reports an **error**. */
  refused: string[]
  /** Not banked because the wording speaks a numbering this document has moved
   *  on from. The remedy is to re-word the segment. */
  stranded: string[]
  /** Not banked because they are held. Checked **first**, so a segment that is
   *  also stranded or failing appears only here. */
  held: string[]
}

export interface ConfigResponse {
  key: string
  /** The **effective** value afterwards. For a `routing.*` key this is the raw
   *  entry — a provider name *or* `{provider, model}` — so render `routing`
   *  below instead, which is resolved. */
  value: unknown
  providers: Provider[]
  routing: Routing
}

// ── what may be written over HTTP ──────────────────────────────────────────

/**
 * The thirteen key patterns `POST /api/config` admits, where `*` stands for
 * exactly one segment. Everything else is `403`, and the list is **closed rather
 * than filtered**: a key is refused by not being on it, so nothing has to be
 * foreseen to be excluded.
 *
 * This copy exists to label a control, never to gate one. **The server is the
 * only gate** — a second copy of its rules in TypeScript is how two surfaces
 * come to disagree about what is writable, which is why even the base-URL
 * acknowledgement is *sent* rather than pre-checked.
 */
export const WRITABLE_KEY_PATTERNS = [
  'providers.*.kind',
  'providers.*.base_url',
  'providers.*.api_key_env',
  'providers.*.model',
  'providers.*.timeout',
  'providers.*.temperature',
  'providers.*.max_tokens',
  'providers.*.retries',
  'batch.size',
  'batch.concurrency',
  'batch.max_repair_rounds',
  'batch.context',
  'routing.*',
] as const

// ── the key registry, and the assertions that pin it ───────────────────────

/**
 * Every endpoint's top-level response keys, verbatim.
 *
 * `tests/test_studio_contract.py` parses this object out of this file's source
 * and compares each row against the contract document's own `**Response**`
 * table — the same extraction `tests/test_contract.py` makes when it compares
 * the document against a live reply. The type assertions below pin each row to
 * its interface, in both directions.
 */
export const RESPONSE_KEYS = {
  'GET /api/state': ['contract_version', 'version', 'cwd', 'targets', 'providers', 'routing', 'docs', 'untracked', 'collisions'],
  'GET /api/doc': ['source', 'lang', 'tone', 'report', 'segments'],
  'GET /api/preview': ['text', 'blocks', 'missing', 'default_out'],
  'GET /api/models': ['provider', 'configured', 'models', 'error'],
  'POST /api/sentences': ['sentences'],
  'POST /api/style': ['source', 'lang', 'tone', 'ids', 'voice', 'voice_notes'],
  'POST /api/suggest': ['source', 'lang', 'tone', 'algorithm', 'cutoff', 'records', 'segments'],
  'POST /api/extract': ['segments', 'reused', 'rejected', 'kept', 'ambiguous', 'replaced', 'waived_source'],
  'POST /api/save': ['applied', 'unknown', 'stored', 'conflicts'],
  'POST /api/hold': ['applied', 'unknown'],
  'POST /api/waive': ['applied', 'unknown', 'stale'],
  'POST /api/check': ['source', 'lang', 'segments', 'translated', 'errors', 'warnings', 'by_rule', 'issues'],
  'POST /api/translate': ['id', 'total', 'route'],
  'POST /api/job': ['id', 'done', 'total', 'applied', 'log', 'failures', 'refused', 'error', 'usage'],
  'POST /api/render': ['wrote', 'missing'],
  'POST /api/commit': ['committed', 'refused', 'stranded', 'held'],
  'POST /api/config': ['key', 'value', 'providers', 'routing'],
} as const satisfies Record<string, readonly string[]>

/** Seventeen. A test asserts this against the document's own endpoint headings. */
export type Endpoint = keyof typeof RESPONSE_KEYS

/**
 * Fails to compile unless `T` is `never`.
 *
 * Each check below is written out with **concrete** type arguments rather than
 * through one generic helper, and that is not verbosity for its own sake: a
 * helper taking `A` and `I` as parameters cannot satisfy `T extends never` for
 * an unresolved `A`, so `tsc` rejects the helper's own declaration and then
 * never evaluates a single one of the seventeen. The check has to be
 * instantiated where the types are known, or it is not a check.
 *
 * They are exported for the same reason `noUnusedLocals` exists: an assertion
 * nothing references is one somebody deletes.
 */
type Never<T extends never> = T
type Keys<E extends Endpoint> = (typeof RESPONSE_KEYS)[E][number]

export type _State = [
  Never<Exclude<Keys<'GET /api/state'>, keyof StateResponse>>,
  Never<Exclude<keyof StateResponse, Keys<'GET /api/state'>>>,
]
export type _Doc = [
  Never<Exclude<Keys<'GET /api/doc'>, keyof DocResponse>>,
  Never<Exclude<keyof DocResponse, Keys<'GET /api/doc'>>>,
]
export type _Preview = [
  Never<Exclude<Keys<'GET /api/preview'>, keyof PreviewResponse>>,
  Never<Exclude<keyof PreviewResponse, Keys<'GET /api/preview'>>>,
]
export type _Models = [
  Never<Exclude<Keys<'GET /api/models'>, keyof ModelsResponse>>,
  Never<Exclude<keyof ModelsResponse, Keys<'GET /api/models'>>>,
]
export type _Sentences = [
  Never<Exclude<Keys<'POST /api/sentences'>, keyof SentencesResponse>>,
  Never<Exclude<keyof SentencesResponse, Keys<'POST /api/sentences'>>>,
]
export type _Style = [
  Never<Exclude<Keys<'POST /api/style'>, keyof StyleResponse>>,
  Never<Exclude<keyof StyleResponse, Keys<'POST /api/style'>>>,
]
export type _Suggest = [
  Never<Exclude<Keys<'POST /api/suggest'>, keyof SuggestResponse>>,
  Never<Exclude<keyof SuggestResponse, Keys<'POST /api/suggest'>>>,
]
export type _Extract = [
  Never<Exclude<Keys<'POST /api/extract'>, keyof ExtractResponse>>,
  Never<Exclude<keyof ExtractResponse, Keys<'POST /api/extract'>>>,
]
export type _Save = [
  Never<Exclude<Keys<'POST /api/save'>, keyof SaveResponse>>,
  Never<Exclude<keyof SaveResponse, Keys<'POST /api/save'>>>,
]
export type _Hold = [
  Never<Exclude<Keys<'POST /api/hold'>, keyof HoldResponse>>,
  Never<Exclude<keyof HoldResponse, Keys<'POST /api/hold'>>>,
]
export type _Waive = [
  Never<Exclude<Keys<'POST /api/waive'>, keyof WaiveResponse>>,
  Never<Exclude<keyof WaiveResponse, Keys<'POST /api/waive'>>>,
]
export type _Check = [
  Never<Exclude<Keys<'POST /api/check'>, keyof CheckResponse>>,
  Never<Exclude<keyof CheckResponse, Keys<'POST /api/check'>>>,
]
export type _Translate = [
  Never<Exclude<Keys<'POST /api/translate'>, keyof TranslateResponse>>,
  Never<Exclude<keyof TranslateResponse, Keys<'POST /api/translate'>>>,
]
/** Against the **record**: that is the shape a poll of a live job answers, and
 *  the shape the document's table describes. The one-key `JobUnknown` is the
 *  documented exception and is not a response shape this pins. */
export type _Job = [
  Never<Exclude<Keys<'POST /api/job'>, keyof JobRecord>>,
  Never<Exclude<keyof JobRecord, Keys<'POST /api/job'>>>,
]
export type _Render = [
  Never<Exclude<Keys<'POST /api/render'>, keyof RenderResponse>>,
  Never<Exclude<keyof RenderResponse, Keys<'POST /api/render'>>>,
]
export type _Commit = [
  Never<Exclude<Keys<'POST /api/commit'>, keyof CommitResponse>>,
  Never<Exclude<keyof CommitResponse, Keys<'POST /api/commit'>>>,
]
export type _Config = [
  Never<Exclude<Keys<'POST /api/config'>, keyof ConfigResponse>>,
  Never<Exclude<keyof ConfigResponse, Keys<'POST /api/config'>>>,
]

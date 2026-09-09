/**
 * The API client. One function per endpoint, nothing else.
 *
 * Two rules govern everything here and both are recorded defects rather than
 * taste:
 *
 * **Throw on a non-2xx, and carry the status.** The predecessor threw on the
 * presence of `error` in a body whatever the status, which is wrong in both
 * directions on this surface. `GET /api/models` answers `200` with `error`
 * beside the data *precisely* so the model control can degrade rather than
 * block, and that throw discarded the whole body including the `configured` the
 * degradation needs. It also made `POST /api/job`'s own failure branch
 * unreachable — a failed run arrived through the catch, which worked by accident
 * until a second endpoint answered the same shape.
 *
 * **The status is the only discriminator there is.** The contract forbids
 * parsing the sentence: `403` means the key is never writable whatever the
 * value, `400` means fix the payload and resend. Everything that branches on a
 * refusal branches on `ApiError.status`.
 */
import type {
  CheckResponse, CommitResponse, ConfigRequest, ConfigResponse, DocAddress,
  DocResponse, ExtractRequest, ExtractResponse, HoldRequest, HoldResponse,
  JobResponse, ModelsResponse, PreviewResponse, RenderRequest, RenderResponse,
  SaveRequest, SaveResponse, SentencesRequest, SentencesResponse, StateResponse,
  StyleRequest, StyleResponse, SuggestRequest, SuggestResponse,
  TranslateRequest, TranslateResponse, WaiveRequest, WaiveResponse,
} from './contract'

/** A refusal from the server, carrying the status a caller branches on. */
export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * Two POST endpoints read a required field by direct subscript and answer with
 * the `repr()` of a Python `KeyError` — `{"error": "'targets'"}` — rather than a
 * sentence. Divergence (6), open. This client always sends both fields, so the
 * shape is unreachable from here; the guard stays because a body that looks like
 * one is not something to show a reviewer as prose.
 */
const READABLE = /^'[a-z_]+'$/

function sentence(body: unknown, status: number): string {
  const raw = body && typeof body === 'object' && 'error' in body
    ? (body as { error: unknown }).error
    : null
  if (typeof raw !== 'string' || !raw.trim() || READABLE.test(raw.trim())) {
    return `The server refused this request (HTTP ${status}).`
  }
  return raw
}

async function read(response: Response): Promise<unknown> {
  // Every error body this server writes is JSON. The two exceptions are the
  // static refusals, which are plain text, and the `501` for a method other than
  // GET or POST, which is the standard library's HTML page — so a client that
  // parses every response as JSON breaks on it. Nothing here sends such a
  // method; the fallback exists so that if something ever does, the reader is
  // told the status rather than shown a JSON parse error.
  try {
    return await response.json()
  } catch {
    return null
  }
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path)
  const body = await read(response)
  if (!response.ok) throw new ApiError(sentence(body, response.status), response.status)
  return body as T
}

async function post<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const body = await read(response)
  if (!response.ok) throw new ApiError(sentence(body, response.status), response.status)
  return body as T
}

const query = (params: Record<string, string>): string =>
  Object.entries(params)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&')

// ── the seventeen ──────────────────────────────────────────────────────────

/** The bootstrap. The only endpoint that needs no document. */
export const getState = (): Promise<StateResponse> => get('/api/state')

/** One document, every segment, issues attached per segment. No side effects —
 *  it runs the check with `persist=False`. */
export const getDoc = (at: DocAddress): Promise<DocResponse> =>
  get(`/api/doc?${query({ src: at.src, lang: at.lang })}`)

/** The rendered document as text **and** as a block map. `fallback` is hardcoded
 *  true here and defaults false on `/api/render`, so the two can differ for any
 *  segment counted in `missing` — divergence (11). Not a render dry-run. */
export const getPreview = (at: DocAddress): Promise<PreviewResponse> =>
  get(`/api/preview?${query({ src: at.src, lang: at.lang })}`)

/**
 * What a configured backend says it serves.
 *
 * **Answers `200` whatever happens** — unreachable, unparseable, keyless, or a
 * malformed routing entry are all a `200` carrying `error`, with `provider` and
 * `configured` still present, because the control this feeds must degrade to a
 * free-text field rather than block a run. Read `error`; never the status.
 *
 * It can block for most of a minute: a llama.cpp router *blocks* while it loads
 * rather than answering `503`. Show a spinner. **Never a "retrying" message** —
 * a slow first request is a slow request.
 */
export const getModels = (provider?: string): Promise<ModelsResponse> =>
  get(`/api/models?${query({ provider: provider ?? '' })}`)

/** Where sentences begin and end, in text the client is holding. The answer is
 *  strings whose concatenation is the input exactly; walk with a cursor. */
export const postSentences = (request: SentencesRequest): Promise<SentencesResponse> =>
  post('/api/sentences', request)

/** What the model is told about this book's voice, for a set of segments. */
export const postStyle = (request: StyleRequest): Promise<StyleResponse> =>
  post('/api/style', request)

/** Near matches from the memory. **Advisory, and there is no apply path** — a
 *  fuzzy hit differs in its placeholder set by definition. */
export const postSuggest = (request: SuggestRequest): Promise<SuggestResponse> =>
  post('/api/suggest', request)

/**
 * Re-parse the source, carrying over what can be carried over.
 *
 * The two fields the server type-checks with nothing are shaped by the caller —
 * see `ExtractRequest`. This function forwards what it is given and defaults
 * nothing: a default written here would be one edit away from being dynamic, and
 * the endpoint already reads an absent key as false. A test asserts that only
 * `store.ts` names either of them.
 */
export const postExtract = (request: ExtractRequest): Promise<ExtractResponse> =>
  post('/api/extract', request)

/** Write reviewed targets, as `human`. An empty or all-blank target is refused
 *  for the **whole** request with a `400`. */
export const postSave = (request: SaveRequest): Promise<SaveResponse> =>
  post('/api/save', request)

/** Hold segments out of every queue that selects work, or return them to it.
 *  Holding requires a non-empty target; lifting carries no such condition. */
export const postHold = (request: HoldRequest): Promise<HoldResponse> =>
  post('/api/hold', request)

/** Stand by a segment's wording, or take that back. Requires a non-empty target
 *  **and** a segment `lx check` reports an error on. */
export const postWaive = (request: WaiveRequest): Promise<WaiveResponse> =>
  post('/api/waive', request)

/** Run the validators and **persist** the result. */
export const postCheck = (at: DocAddress): Promise<CheckResponse> =>
  post('/api/check', at)

/** Start a run. `200` means the job was accepted, never that the translation
 *  succeeded — everything after that is `/api/job` and nowhere else. */
export const postTranslate = (request: TranslateRequest): Promise<TranslateResponse> =>
  post('/api/translate', request)

/** Poll a run. An id with no record answers `200` with one key; tell the two
 *  apart with `isJobRecord`, never by the presence of `error`. */
export const postJob = (id: string): Promise<JobResponse> =>
  post('/api/job', { id })

/** Render and **write a file** — the only endpoint that writes outside `.lx/`. */
export const postRender = (request: RenderRequest): Promise<RenderResponse> =>
  post('/api/render', request)

/** Bank the document's approved wordings into the translation memory. */
export const postCommit = (at: DocAddress): Promise<CommitResponse> =>
  post('/api/commit', at)

/**
 * Write or remove **one** configuration key.
 *
 * One key per request, and the server serializes them: a payload carrying
 * several keys is a block write wearing a different hat, and two requests
 * arriving together would each read the file, set one key and write it back.
 * A caller sending several fields therefore orders them so that **any prefix of
 * the sequence is a coherent configuration**, and stops at the first refusal.
 */
export const postConfig = (request: ConfigRequest): Promise<ConfigResponse> =>
  post('/api/config', request)

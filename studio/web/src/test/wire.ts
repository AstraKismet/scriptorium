/**
 * A stand-in for the wire, so a test can say what the server answered.
 *
 * The Python suite drives the real server in-process and proves what it does;
 * nothing there can prove what the *page* sends, because until now nothing in
 * this repository executed the frontend's JavaScript at all. These tests are
 * that half: they assert the exact request bodies the store builds, which is
 * where the destructive mistakes on this surface live — a `reset` that is a
 * string, a `limit` sent beside `ids`, a `model` sent when nobody chose one.
 */
export interface Call {
  path: string
  method: string
  body: unknown
}

export interface Answer {
  status?: number
  body: unknown
  /** Hold the reply until this settles, so a test can look at the page while
   *  the request is still in flight. Everything else here answers at once. */
  after?: Promise<void>
}

let queued: Answer[] = []
let fallback: Answer = { body: {} }
let byPath: ((call: Call) => Answer | null) | null = null

export const calls: Call[] = []

/** Answer the next request, then the one after that, and so on. */
export function replies(...answers: Answer[]): void {
  queued = [...answers]
}

/** Answer anything the queue does not cover. */
export function otherwise(answer: Answer): void {
  fallback = answer
}

/**
 * Answer by looking at the request, and outrank the queue while doing it.
 *
 * A whole-application test cannot use `replies`: the margin and the model list
 * fetch on timers of their own, so a queue sooner or later hands a document to a
 * style request. The alternative a test file reaches for is its own
 * `globalThis.fetch`, and that is what this exists to stop — a second stub is a
 * second record of what was sent, `calls` no longer sees it, and `callsTo` /
 * `lastCall` silently answer about nothing. Returning `null` falls through to
 * the queue and then the fallback.
 */
export function answering(fn: (call: Call) => Answer | null): void {
  byPath = fn
}

export function install(): void {
  calls.length = 0
  queued = []
  fallback = { body: {} }
  byPath = null
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const method = init?.method ?? 'GET'
    const raw = init?.body
    const call: Call = {
      path,
      method,
      body: typeof raw === 'string' ? JSON.parse(raw) : null,
    }
    calls.push(call)
    const answer = byPath?.(call) ?? queued.shift() ?? fallback
    const status = answer.status ?? 200
    const reply = {
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(answer.body),
    } as Response
    return answer.after ? answer.after.then(() => reply) : Promise.resolve(reply)
  }) as typeof fetch
}

export const lastCall = (path: string): Call | undefined =>
  [...calls].reverse().find(c => c.path.startsWith(path))

export const callsTo = (path: string): Call[] =>
  calls.filter(c => c.path.startsWith(path))

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
}

let queued: Answer[] = []
let fallback: Answer = { body: {} }

export const calls: Call[] = []

/** Answer the next request, then the one after that, and so on. */
export function replies(...answers: Answer[]): void {
  queued = [...answers]
}

/** Answer anything the queue does not cover. */
export function otherwise(answer: Answer): void {
  fallback = answer
}

export function install(): void {
  calls.length = 0
  queued = []
  fallback = { body: {} }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const method = init?.method ?? 'GET'
    const raw = init?.body
    calls.push({
      path,
      method,
      body: typeof raw === 'string' ? JSON.parse(raw) : null,
    })
    const answer = queued.shift() ?? fallback
    const status = answer.status ?? 200
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(answer.body),
    } as Response)
  }) as typeof fetch
}

export const lastCall = (path: string): Call | undefined =>
  [...calls].reverse().find(c => c.path.startsWith(path))

export const callsTo = (path: string): Call[] =>
  calls.filter(c => c.path.startsWith(path))

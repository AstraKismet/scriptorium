/**
 * A yes/no the caller can `await`.
 *
 * `window.confirm` was the alternative and is refused twice over: it blocks the
 * event loop, so a `/api/job` poll already in flight stops answering while it is
 * up, and it renders one line of plain text where every act that reaches this
 * has a mechanism to explain. Nothing destructive on this surface happens
 * without a person reading what it costs.
 *
 * **`false` is the only answer Escape and the backdrop can give**, and the
 * handler that provides it is detached before a button closes the dialog rather
 * than left to be out-raced: `close` is queued rather than dispatched
 * synchronously, so relying on that ordering works and depends on a spec detail
 * no reader of this file should have to know.
 */
import { useEffect, useRef, useState } from 'react'

interface Request {
  title: string
  body: string
  yes: string
  /** The button that says yes is drawn in gold — the pigment for what is
   *  banked. A destructive act gets rubric instead. */
  destructive?: boolean
  settle: (answer: boolean) => void
}

let present: ((r: Request | null) => void) | null = null

export function ask(
  title: string,
  body: string,
  yes: string,
  destructive = false,
): Promise<boolean> {
  if (!present) return Promise.resolve(false)
  return new Promise<boolean>(settle => {
    present?.({ title, body, yes, destructive, settle })
  })
}

export function Confirm() {
  const [request, setRequest] = useState<Request | null>(null)
  const dialog = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    present = setRequest
    return () => { present = null }
  }, [])

  useEffect(() => {
    const el = dialog.current
    if (!el) return
    if (request && !el.open) el.showModal()
    if (!request && el.open) el.close()
  }, [request])

  const finish = (answer: boolean) => {
    const settle = request?.settle
    setRequest(null)
    settle?.(answer)
  }

  return (
    <dialog
      ref={dialog}
      // Covers Escape and the backdrop. It fires only when the dialog closes
      // without a button having answered, because `finish` clears the request
      // first — so the only path to `true` is the control that says so.
      onClose={() => { if (request) finish(false) }}
      onCancel={e => { e.preventDefault(); finish(false) }}
    >
      {request && (
        <>
          <header><h3>{request.title}</h3></header>
          <pre>{request.body}</pre>
          <div className="form" style={{ padding: '0 18px 16px' }}>
            <footer>
              <button type="button" onClick={() => finish(false)} autoFocus>Cancel</button>
              <span className="spacer" style={{ flex: 1 }} />
              <button
                type="button"
                className="key"
                style={request.destructive ? { borderColor: 'var(--rubric)', color: 'var(--rubric)' } : undefined}
                onClick={() => finish(true)}
              >
                {request.yes}
              </button>
            </footer>
          </div>
        </>
      )}
    </dialog>
  )
}

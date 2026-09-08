/**
 * The client's one rule, and the two ways the page it replaces got it wrong.
 *
 * Throw on a non-2xx and carry the status; never throw on the *presence* of
 * `error` in a body. The predecessor did the latter, which is wrong in both
 * directions on this surface: `GET /api/models` answers `200` with `error`
 * beside the data precisely so a control can degrade rather than block, and that
 * throw discarded the whole body including the `configured` the degradation
 * needs — while making `POST /api/job`'s own failure branch unreachable.
 */
import { describe, expect, it } from 'vitest'

import * as api from './api'
import { ApiError } from './api'
import { isJobRecord } from './contract'
import { replies } from './test/wire'

describe('a 200 carrying error', () => {
  it('is data on /api/models, not a failure', async () => {
    replies({ body: { provider: 'local', configured: 'qwen', models: [], error: 'connection refused' } })
    const answer = await api.getModels('local')
    expect(answer.error).toBe('connection refused')
    // The degradation path: the control falls back to free text *carrying*
    // `configured`, so throwing here would take away the one thing it needs.
    expect(answer.configured).toBe('qwen')
  })

  it('is a job record when it carries `done`, and an unknown id when it does not', async () => {
    replies({ body: { id: 'j1', done: false, total: 3, applied: 0, log: [], failures: [], refused: [], error: null, usage: { prompt: 0, completion: 0, total: 0, replies: 0, reported: 0 } } })
    const live = await api.postJob('j1')
    // Told apart by the absence of `done`, never by the presence of `error`: a
    // live record carries `error: null` throughout and a failed one carries a
    // sentence while still being a record.
    expect(isJobRecord(live)).toBe(true)

    replies({ body: { error: 'no such job' } })
    expect(isJobRecord(await api.postJob('j9'))).toBe(false)
  })
})

describe('a refusal', () => {
  it('throws and carries the status a caller branches on', async () => {
    replies({ status: 403, body: { error: 'providers.x.headers is never writable over HTTP' } })
    await expect(api.postConfig({ key: 'providers.x.headers', value: 1 }))
      .rejects.toBeInstanceOf(ApiError)

    replies({ status: 403, body: { error: 'never writable' } })
    const caught = await api.postConfig({ key: 'k', value: 1 }).catch((e: unknown) => e)
    // 403 means the key is never writable whatever the value; 400 means fix the
    // payload and send it again. The contract forbids parsing the sentence, so
    // the status is the only discriminator there is.
    expect((caught as ApiError).status).toBe(403)
  })

  it('does not show a bare KeyError repr to a person', async () => {
    // Two endpoints read a required field by direct subscript and answer with
    // the repr of a Python KeyError — `{"error": "'targets'"}` — rather than a
    // sentence. Divergence (6), open.
    replies({ status: 400, body: { error: "'targets'" } })
    const caught = await api.postSave({ src: 'a', lang: 'zh-TW', targets: {} }).catch((e: unknown) => e)
    expect((caught as Error).message).not.toBe("'targets'")
    expect((caught as Error).message).toContain('400')
  })
})

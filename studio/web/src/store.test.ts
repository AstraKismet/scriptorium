/**
 * What the page actually sends.
 *
 * Every assertion here is a request body, and each one stands in front of a
 * measured defect on this surface. The Python suite proves what the server does
 * with a body; only this can prove which body the page builds — and until the
 * rebuild, nothing in this repository executed the frontend's JavaScript at all,
 * so the one guard that existed was a regex over a file.
 */
import { beforeEach, describe, expect, it } from 'vitest'

import * as drafts from './drafts'
import { useStore, visible } from './store'
import { callsTo, lastCall, otherwise, replies } from './test/wire'
import type { DocResponse, Segment, StateResponse } from './contract'

const initial = useStore.getState()

const segment = (over: Partial<Segment> = {}): Segment => ({
  id: 's0001',
  kind: 'para',
  status: 'translated',
  origin: 'llm:draft',
  source: 'She had not slept.',
  target: '她沒有睡。',
  review: null,
  waived: false,
  token: 'aaaaaaaaaaaa',
  issues: [],
  ...over,
})

const doc = (segments: Segment[]): DocResponse => ({
  source: 'book/ch1.md',
  lang: 'zh-TW',
  tone: 'literary',
  report: { segments: segments.length, translated: segments.filter(s => s.target).length, errors: 0, warnings: 0, by_rule: {} },
  segments,
})

const state = (over: Partial<StateResponse> = {}): StateResponse => ({
  contract_version: 4,
  version: '0.4.0',
  cwd: '/books',
  targets: ['zh-TW'],
  providers: [],
  routing: {},
  docs: [],
  untracked: [],
  collisions: [],
  ...over,
})

/** Put a document on screen without going through the wire twice. */
async function openWith(segments: Segment[]): Promise<void> {
  replies({ body: doc(segments) })
  await useStore.getState().open('book/ch1.md', 'zh-TW')
}

beforeEach(() => {
  useStore.setState(initial, true)
})

describe('the register, which the server type-checks with nothing', () => {
  it('sends neither reset nor tone on a plain re-extract', async () => {
    await openWith([segment()])
    replies({ body: { segments: 1, reused: 1, rejected: 0, kept: [], ambiguous: [], replaced: [], waived_source: [] } })
    otherwise({ body: doc([segment()]) })
    await useStore.getState().extract()

    const sent = callsTo('/api/extract')[0]?.body as Record<string, unknown>
    expect(sent).toEqual({ src: 'book/ch1.md', lang: 'zh-TW' })
    expect('reset' in sent).toBe(false)
    expect('tone' in sent).toBe(false)
  })

  it('sends a JSON boolean and a chosen register on a start-over', async () => {
    await openWith([segment()])
    replies({ body: { segments: 1, reused: 0, rejected: 0, kept: [], ambiguous: [], replaced: [], waived_source: [] } })
    otherwise({ body: doc([segment({ target: '' , status: 'pending' })]) })
    await useStore.getState().startOver('literary')

    const sent = callsTo('/api/extract')[0]?.body as Record<string, unknown>
    // The type is the assertion. `reset` is read for truthiness in Python, so
    // the *string* "false" is a reset that discards the document's translations
    // and there is no server-side guard against it.
    expect(typeof sent.reset).toBe('boolean')
    expect(sent.reset).toBe(true)
    expect(sent.tone).toBe('literary')
  })
})

describe('saving', () => {
  it('sends the token each edit was based on', async () => {
    await openWith([segment({ id: 's0001', token: 'tok1' })])
    drafts.set('s0001', '她一夜沒睡。', '她沒有睡。')
    replies({ body: { applied: 1, unknown: [], stored: { s0001: { text: '她一夜沒睡。', token: 'tok2' } }, conflicts: {} } })
    otherwise({ body: doc([segment({ id: 's0001', target: '她一夜沒睡。', token: 'tok2' })]) })

    await useStore.getState().save()
    const sent = lastCall('/api/save')?.body as { targets: unknown; base: unknown }
    expect(sent.targets).toEqual({ s0001: '她一夜沒睡。' })
    expect(sent.base).toEqual({ s0001: 'tok1' })
  })

  it('holds a blank back rather than poisoning the whole request', async () => {
    // The server refuses an empty target for the WHOLE request, and this is one
    // map for the whole ledger — so one cleared segment would refuse every later
    // edit in the same session, over and over, because the poisoned batch keeps
    // being resent.
    await openWith([segment({ id: 's0001' }), segment({ id: 's0002', target: '第二段。', token: 'tok2' })])
    drafts.set('s0001', '   ', '她沒有睡。')
    drafts.set('s0002', '第二段改過了。', '第二段。')
    replies({ body: { applied: 1, unknown: [], stored: { s0002: { text: '第二段改過了。', token: 'x' } }, conflicts: {} } })
    otherwise({ body: doc([segment()]) })

    await useStore.getState().save()
    const sent = lastCall('/api/save')?.body as { targets: Record<string, string> }
    expect(Object.keys(sent.targets)).toEqual(['s0002'])
    // The blank stays dirty and stays on screen; only what was sent is forgotten.
    expect(drafts.has('s0001')).toBe(true)
    expect(drafts.has('s0002')).toBe(false)
  })

  it('keeps every edit when the request is refused', async () => {
    await openWith([segment({ id: 's0001' })])
    drafts.set('s0001', '改過的句子。', '她沒有睡。')
    replies({ status: 400, body: { error: 'an empty target is not storable' } })

    await useStore.getState().save()
    expect(drafts.has('s0001')).toBe(true)
  })
})

describe('starting a run', () => {
  const nothingToDo = { body: { id: 'job1', total: 0, route: { provider: 'local', model: 'm' } } }

  it('sends neither provider nor model when nobody chose one', async () => {
    await openWith([segment()])
    replies(nothingToDo)
    await useStore.getState().runJob('polish')

    const sent = lastCall('/api/translate')?.body as Record<string, unknown>
    // A seeded box is draft's answer riding on Polish and Repair: `resolve_route`
    // puts the caller's model first, and a `provider` that differs from the
    // stage's own drops that stage's model with it.
    expect('provider' in sent).toBe(false)
    expect('model' in sent).toBe(false)
  })

  it('sends the bound as a number, and omits it at zero', async () => {
    await openWith([segment()])
    useStore.setState({ limit: 25 })
    replies(nothingToDo)
    await useStore.getState().runJob('draft')
    expect((lastCall('/api/translate')?.body as { limit: unknown }).limit).toBe(25)

    useStore.setState({ limit: 0, running: false })
    replies(nothingToDo)
    await useStore.getState().runJob('draft')
    expect('limit' in (lastCall('/api/translate')?.body as object)).toBe(false)
  })

  it('never sends the bound beside named ids', async () => {
    // The bound is documented as ignored there and is still *checked*, so the
    // only thing sending it can do is refuse a request that would have ignored it.
    await openWith([segment()])
    useStore.setState({ limit: 25 })
    replies(nothingToDo)
    await useStore.getState().runJob('draft', ['s0001'])

    const sent = lastCall('/api/translate')?.body as Record<string, unknown>
    expect(sent.ids).toEqual(['s0001'])
    expect('limit' in sent).toBe(false)
  })

  it('never defaults overwrite_human on', async () => {
    await openWith([segment()])
    replies(nothingToDo)
    await useStore.getState().runJob('draft', ['s0001'])
    expect('overwrite_human' in (lastCall('/api/translate')?.body as object)).toBe(false)
  })

  it('refuses to start a second run while one is in flight', async () => {
    await openWith([segment()])
    useStore.setState({ running: true })
    await useStore.getState().runJob('draft')
    expect(callsTo('/api/translate')).toHaveLength(0)
  })
})

describe('the contract gate', () => {
  it('refuses a version it does not know, rather than degrading', async () => {
    replies({ body: state({ contract_version: 99 }) })
    await useStore.getState().bootstrap()
    expect(useStore.getState().boot).toBe('incompatible')
    expect(useStore.getState().serverContract).toBe(99)
  })

  it('starts on the version it was written against', async () => {
    replies({ body: state() })
    otherwise({ body: { provider: '', configured: '', models: [], error: null } })
    await useStore.getState().bootstrap()
    expect(useStore.getState().boot).toBe('ready')
  })
})

describe('the filter', () => {
  const rows = [
    segment({ id: 's1', target: '', status: 'pending' }),
    segment({ id: 's2', issues: [{ seg: 's2', rule: 'tags', severity: 'error', message: 'x' }] }),
    segment({ id: 's3', review: 'held' }),
    segment({ id: 's4', waived: true }),
    segment({ id: 's5', issues: [{ seg: 's5', rule: 'held', severity: 'warn', message: 'x' }] }),
  ]
  const d = doc(rows)

  it('reads a field the segment already carries and computes nothing else', () => {
    expect(visible(d, 'all').map(s => s.id)).toEqual(['s1', 's2', 's3', 's4', 's5'])
    expect(visible(d, 'pending').map(s => s.id)).toEqual(['s1'])
    // A warning is not an error, whatever its severity string says.
    expect(visible(d, 'failing').map(s => s.id)).toEqual(['s2'])
    expect(visible(d, 'held').map(s => s.id)).toEqual(['s3'])
    expect(visible(d, 'waived').map(s => s.id)).toEqual(['s4'])
  })
})

/**
 * What the page actually sends.
 *
 * Every assertion here is a request body, and each one stands in front of a
 * measured defect on this surface. The Python suite proves what the server does
 * with a body; only this can prove which body the page builds — and until the
 * rebuild, nothing in this repository executed the frontend's JavaScript at all,
 * so the one guard that existed was a regex over a file.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as drafts from './drafts'
import { SAVE_WAIT_MS, useStore, visible, writeEpoch } from './store'
import { answering, callsTo, lastCall, otherwise, replies } from './test/wire'
import { CONTRACT_VERSION } from './contract'
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
  contract_version: CONTRACT_VERSION,
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

/** The document `openWith` puts on screen, as the address an act names it by. */
const ch1 = { src: 'book/ch1.md', lang: 'zh-TW' }

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
    await useStore.getState().extract(ch1)

    const sent = callsTo('/api/extract')[0]?.body as Record<string, unknown>
    expect(sent).toEqual({ src: 'book/ch1.md', lang: 'zh-TW' })
    expect('reset' in sent).toBe(false)
    expect('tone' in sent).toBe(false)
  })

  it('sends a JSON boolean and a chosen register on a start-over', async () => {
    await openWith([segment()])
    replies({ body: { segments: 1, reused: 0, rejected: 0, kept: [], ambiguous: [], replaced: [], waived_source: [] } })
    otherwise({ body: doc([segment({ target: '' , status: 'pending' })]) })
    await useStore.getState().startOver(ch1, 'literary')

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

  it('re-reads the document it stays on, so the next edit carries a fresh token', async () => {
    // `save()` skips the re-read only when the page is leaving the document. On
    // the ordinary path — a blur, Ctrl+Enter — it must still happen, or every
    // token on screen is one generation stale and the reviewer's next edit of
    // the same row conflicts with their own write.
    await openWith([segment({ id: 's0001', token: 'tok1' })])
    drafts.set('s0001', '她一夜沒睡。', '她沒有睡。')
    replies(
      { body: { applied: 1, unknown: [], stored: {}, conflicts: {} } },
      { body: doc([segment({ id: 's0001', target: '她一夜沒睡。', token: 'tok2' })]) },
    )
    await useStore.getState().save()
    expect(callsTo('/api/doc')).toHaveLength(2)
    expect(useStore.getState().doc?.segments[0]?.token).toBe('tok2')
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

/**
 * The other half of "written before it is discarded": the act that makes
 * writing impossible.
 *
 * A parse reassigns ids from `s0001`, so the moment the server accepts a
 * re-extract every unsaved edit is keyed on a number that now names some other
 * paragraph. Flushing one there would write a reviewer's sentence onto a
 * paragraph nobody chose, with matching placeholders and a green `lx check` —
 * and the lost-update token cannot catch it, because `sha1("")` hashes an
 * absent target and an empty one alike and a re-parse leaves runs of
 * untranslated segments.
 *
 * These pass against `e6fc06d` too, because nothing there flushed at all. They
 * are the guard for the shape that now does.
 */
describe('a re-parse voids every unsaved edit, at the moment the server accepts it', () => {
  const extracted = {
    segments: 1, reused: 0, rejected: 0,
    kept: [], ambiguous: [], replaced: [], waived_source: [],
  }

  it('never writes an edit into the numbering a re-extract has just replaced', async () => {
    await openWith([segment({ id: 's0001' })])
    drafts.set('s0001', '改過的句子。', '她沒有睡。')
    replies({ body: extracted })
    otherwise({ body: doc([segment({ id: 's0001' })]) })

    await useStore.getState().extract(ch1)
    expect(callsTo('/api/save')).toHaveLength(0)
    expect(drafts.size()).toBe(0)
  })

  it('does not post the words a start-over was told to discard', async () => {
    // The path with no save in front of it: the toolbar's plain Re-extract
    // saves first and refuses the act while anything is left over, and Start
    // over asks no such question. Its reviewer confirmed discarding the
    // document's translations, not the sentence they were part-way through.
    await openWith([segment({ id: 's0001' })])
    drafts.set('s0001', '改過的句子。', '她沒有睡。')
    replies({ body: extracted })
    otherwise({ body: doc([segment({ id: 's0001', target: '', status: 'pending' })]) })

    await useStore.getState().startOver(ch1, 'literary')
    expect(callsTo('/api/save')).toHaveLength(0)
    expect(drafts.size()).toBe(0)
    // Gone, and said to be gone. Before this they vanished without a line.
    expect(useStore.getState().log.some(l => l.level === 'bad' && l.text.includes('s0001'))).toBe(true)
  })

  it('holds across the reload, because the ledger is mounted for two more round trips', async () => {
    // Emptying the map after the re-parse is not enough, and this is why: what
    // follows is `GET /api/state` and then the document's own fetch, with the
    // ledger still on screen the whole time. A keystroke arriving in that
    // window is keyed on the parse that has just gone, and looks exactly like
    // one that arrived before it.
    await openWith([segment({ id: 's0001' })])
    answering(call => {
      if (call.path.startsWith('/api/state')) drafts.set('s0001', '打在重編號之後。', '她沒有睡。')
      return null
    })
    replies({ body: extracted })
    otherwise({ body: doc([segment({ id: 's0001' })]) })

    await useStore.getState().extract(ch1)
    expect(callsTo('/api/save')).toHaveLength(0)
    expect(drafts.size()).toBe(0)
  })

  it('refuses a blur that lands in the same window, not only the re-open', async () => {
    // The field is still mounted and still saves on blur. The mark used to be
    // read only by `open()`, so a keystroke and a blur during the reload posted
    // the sentence under the renumbered id — found by an adversarial pass over
    // this change, and failing on its first commit. `save()` reads it now.
    await openWith([segment({ id: 's0001' })])
    answering(call => {
      if (call.path.startsWith('/api/state')) {
        drafts.set('s0001', '打在重編號之後。', '她沒有睡。')
        void useStore.getState().save()
      }
      return null
    })
    replies({ body: extracted })
    otherwise({ body: doc([segment({ id: 's0001' })]) })

    await useStore.getState().extract(ch1)
    expect(callsTo('/api/save')).toHaveLength(0)
  })

  it('leaves later edits writable after a re-extract that had nothing to void', async () => {
    // The mark is lowered by `clear()`, and it must be lowered even when the map
    // is already empty — otherwise the next edit after an ordinary re-extract is
    // never written by anything, and leaving the document names it as a casualty
    // of a re-parse that happened before it was typed.
    await openWith([segment({ id: 's0001' })])
    replies({ body: extracted })
    otherwise({ body: doc([segment({ id: 's0001' })]) })
    await useStore.getState().extract(ch1)

    drafts.set('s0001', '重新抽取之後才打的。', '她沒有睡。')
    otherwise({ body: { applied: 1, unknown: [], stored: {}, conflicts: {} } })
    replies({ body: { applied: 1, unknown: [], stored: {}, conflicts: {} } }, { body: doc([segment()]) })
    await useStore.getState().open('book/ch2.md', 'zh-TW')
    expect((lastCall('/api/save')?.body as { targets: unknown }).targets)
      .toEqual({ s0001: '重新抽取之後才打的。' })
  })

  /*
   * Which document a re-extract is about is the caller's to say, and it used to
   * be read off the screen: `shown() ?? at`. The rail's *Not yet extracted*
   * entry then re-extracted the document that was open instead of the file that
   * was clicked (HANDOFF-088). What the screen still decides is what the
   * re-parse does to this page, and the four tests below are that half.
   */

  it('sends the document the caller names, whatever is on screen', async () => {
    await openWith([segment({ id: 's0001' })])
    replies({ body: extracted })
    otherwise({ body: state() })
    expect(await useStore.getState().extract({ src: 'docs/new.md', lang: 'zh-TW' })).toBe(true)
    expect(callsTo('/api/extract').map(c => c.body)).toEqual([{ src: 'docs/new.md', lang: 'zh-TW' }])
  })

  it('leaves the words on screen writable when it re-parses another document', async () => {
    // Nothing they are keyed on was renumbered, so marking them would throw
    // them away as casualties of a parse that never touched them — and the
    // rail's entry, which extracts a file that is not open, would do it on
    // every click made with a sentence unsaved.
    await openWith([segment({ id: 's0001' })])
    drafts.set('s0001', '改過的句子。', '她沒有睡。')
    replies({ body: extracted })
    otherwise({ body: state() })
    await useStore.getState().extract({ src: 'docs/new.md', lang: 'zh-TW' })

    expect(drafts.stranded()).toBe(false)
    expect(drafts.get('s0001')).toBe('改過的句子。')
    // And it opened nothing: the address names `book/ch1.md`, and a document the
    // address does not name is the address's to open.
    expect(callsTo('/api/doc')).toHaveLength(1)
    expect(useStore.getState().doc?.source).toBe('book/ch1.md')
    expect(useStore.getState().at).toEqual(ch1)
  })

  it('voids the words when the document re-parsed is the one they were typed in, even after the address moved', async () => {
    // `at` and `shown()` differ for a whole round trip while an open is in
    // flight, with the ledger still mounted. The words belong to the parse on
    // screen, so it is the screen that decides whether they are void — and the
    // address that decides nothing is re-opened: that is the effect's, for the
    // document the reviewer went to.
    await openWith([segment({ id: 's0001' })])
    drafts.set('s0001', '改過的句子。', '她沒有睡。')
    useStore.setState({ at: { src: 'book/ch9.md', lang: 'zh-TW' } })
    replies({ body: extracted })
    otherwise({ body: state() })
    await useStore.getState().extract(ch1)

    expect(callsTo('/api/extract')[0]?.body).toEqual(ch1)
    expect(drafts.stranded()).toBe(true)
    expect(callsTo('/api/save')).toHaveLength(0)
    expect(callsTo('/api/doc')).toHaveLength(1)
  })

  it('re-reads a document the address names even when another is on screen', async () => {
    // A hand-typed link to a file with no state leaves `doc` on the document
    // before it while `at` names the file. Once the extract has made the file
    // exist, reading it is exactly what that address was asking for.
    await openWith([segment({ id: 's0001' })])
    useStore.setState({ at: { src: 'docs/new.md', lang: 'zh-TW' } })
    replies({ body: extracted }, { body: state() })
    otherwise({ body: { ...doc([segment()]), source: 'docs/new.md' } })
    await useStore.getState().extract({ src: 'docs/new.md', lang: 'zh-TW' })

    expect(useStore.getState().doc?.source).toBe('docs/new.md')
    expect(drafts.stranded()).toBe(false)
  })

  it('answers false and opens nothing when the server refuses the extract', async () => {
    await openWith([segment({ id: 's0001' })])
    replies({ status: 400, body: { error: 'not UTF-8' } })
    expect(await useStore.getState().extract(ch1)).toBe(false)
    expect(callsTo('/api/doc')).toHaveLength(1)
    expect(useStore.getState().running).toBe(false)
    expect(useStore.getState().log.some(l => l.level === 'bad' && l.text.includes('not UTF-8'))).toBe(true)
  })

  it('sends one extract when a second is asked for while the first is in flight', async () => {
    // The flag is raised before the first await, and that ordering is the whole
    // of the guard: raised after the request, two calls in one tick both pass it
    // and both are sent. A mutation lane moved it and nothing failed.
    await openWith([segment({ id: 's0001' })])
    let release: () => void = () => undefined
    replies({ body: extracted, after: new Promise<void>(r => { release = r }) }, { body: state() })
    otherwise({ body: doc([segment()]) })
    const first = useStore.getState().extract(ch1)
    expect(await useStore.getState().extract(ch1)).toBe(false)
    release()
    expect(await first).toBe(true)
    expect(callsTo('/api/extract')).toHaveLength(1)
  })

  it('answers false and sends nothing while a run is in flight', async () => {
    await openWith([segment({ id: 's0001' })])
    useStore.setState({ running: true })
    expect(await useStore.getState().extract(ch1)).toBe(false)
    expect(callsTo('/api/extract')).toHaveLength(0)
    // Still running: refusing the extract must not clear the flag of the run
    // that refused it.
    expect(useStore.getState().running).toBe(true)
  })

  it('does not keep a reviewer on a document over words nothing can write', async () => {
    // Stranded words are unwritable anywhere, so declining to leave over them
    // would hold the reviewer for nothing. They go, and they are named.
    await openWith([segment({ id: 's0001' })])
    drafts.set('s0001', '改過的句子。', '她沒有睡。')
    drafts.strand()
    replies({ body: doc([segment()]) })
    await useStore.getState().open('book/ch2.md', 'zh-TW')

    expect(useStore.getState().docError).toBe('')
    expect(useStore.getState().doc).not.toBeNull()
    expect(callsTo('/api/save')).toHaveLength(0)
    expect(drafts.size()).toBe(0)
    expect(useStore.getState().log.some(l => l.level === 'bad' && l.text.includes('s0001'))).toBe(true)
  })
})

describe('reading again', () => {
  it('moves the write epoch the moment a save sends, before any reply', async () => {
    // Re-extract reads the epoch after awaits of its own; a save still on its
    // way at that moment must already have moved it, or its wording is not in
    // the count and nothing says so.
    await openWith([segment({ id: 's0001' })])
    drafts.set('s0001', '改過的句子。', '她沒有睡。')
    replies({ body: { applied: 1, unknown: [], stored: {}, conflicts: {} }, after: new Promise<void>(() => undefined) })
    const before = writeEpoch()
    void useStore.getState().save()
    expect(writeEpoch()).not.toBe(before)
  })

  it('answers false from a refresh with nothing on screen, and reads nothing', async () => {
    // The toolbar reads the answer to decide whether it may trust a count; a
    // refresh that read nothing must not say it did.
    expect(await useStore.getState().refresh()).toBe(false)
    expect(callsTo('/api/doc')).toHaveLength(0)
  })

  it('asks the server nothing for the untracked page\'s extract while a run is in flight', async () => {
    // The button is disabled then, which is what every click test sees; this
    // is the guard behind it, for a caller that is not that button.
    useStore.setState({ state: state({ untracked: [{ source: 'docs/new.md', lang: 'zh-TW' }] }), running: true })
    expect(await useStore.getState().extractUntracked({ src: 'docs/new.md', lang: 'zh-TW' })).toBe(false)
    expect(callsTo('/api/state')).toHaveLength(0)
    expect(callsTo('/api/extract')).toHaveLength(0)
  })

  it('does not swallow a click on another untracked file made while the first is asking', async () => {
    // One flag for every file swallowed the second click whole — no request and
    // no line — while the first file's extract then logged its own header: the
    // symptom HANDOFF-088 was about, through a new door.
    const listed = state({ untracked: [{ source: 'docs/x.md', lang: 'zh-TW' }, { source: 'docs/y.md', lang: 'zh-TW' }] })
    useStore.setState({ state: listed })
    let release: () => void = () => undefined
    const gate = new Promise<void>(r => { release = r })
    answering(call => (call.path.startsWith('/api/state') ? { body: listed, after: gate }
      : call.path.startsWith('/api/extract')
        ? { body: { segments: 1, reused: 0, rejected: 0, kept: [], ambiguous: [], replaced: [], waived_source: [] } }
        : null))
    const x = useStore.getState().extractUntracked({ src: 'docs/x.md', lang: 'zh-TW' })
    const y = useStore.getState().extractUntracked({ src: 'docs/y.md', lang: 'zh-TW' })
    release()
    await Promise.all([x, y])

    expect(callsTo('/api/extract').map(c => (c.body as { src: string }).src)).toEqual(['docs/x.md'])
    expect(useStore.getState().log.some(l => l.text.includes('docs/y.md') && l.text.includes('was not extracted'))).toBe(true)
  })

  it('does not swallow a click on the same file in another language made while the first is asking', async () => {
    // The key is the pair, and each half is load-bearing: a key of the path
    // alone swallowed the second language's click the way one flag for every
    // file swallowed the second file's.
    const listed = state({
      targets: ['zh-TW', 'ja'],
      untracked: [{ source: 'docs/x.md', lang: 'zh-TW' }, { source: 'docs/x.md', lang: 'ja' }],
    })
    useStore.setState({ state: listed })
    let release: () => void = () => undefined
    const gate = new Promise<void>(r => { release = r })
    answering(call => (call.path.startsWith('/api/state') ? { body: listed, after: gate }
      : call.path.startsWith('/api/extract')
        ? { body: { segments: 1, reused: 0, rejected: 0, kept: [], ambiguous: [], replaced: [], waived_source: [] } }
        : null))
    const zh = useStore.getState().extractUntracked({ src: 'docs/x.md', lang: 'zh-TW' })
    const ja = useStore.getState().extractUntracked({ src: 'docs/x.md', lang: 'ja' })
    release()
    await Promise.all([zh, ja])

    expect(callsTo('/api/state')).toHaveLength(3)
    expect(useStore.getState().log.some(l => l.text.includes('docs/x.md was not extracted'))).toBe(true)
  })

  it('answers false when the file turned out to be extracted already, having sent no extract', async () => {
    useStore.setState({ state: state({ untracked: [{ source: 'docs/new.md', lang: 'zh-TW' }] }) })
    replies({ body: state() })
    expect(await useStore.getState().extractUntracked({ src: 'docs/new.md', lang: 'zh-TW' })).toBe(false)
    expect(callsTo('/api/extract')).toHaveLength(0)
  })
})

describe('opening a document', () => {
  it('does not paint a superseded open\'s failure over the document that won', async () => {
    // Two opens in flight, the first one failing after the second has been
    // asked for. `App.tsx` draws `docError` before anything else, so a stale
    // failure would put an error screen over a chapter the reviewer is on.
    answering(call =>
      call.path.startsWith('/api/doc') && new URLSearchParams(call.path.split('?')[1]).get('src') === 'book/ch9.md'
        ? { status: 404, body: { error: 'no such document' } }
        : null)
    otherwise({ body: doc([segment()]) })
    await Promise.all([
      useStore.getState().open('book/ch9.md', 'zh-TW'),
      useStore.getState().open('book/ch1.md', 'zh-TW'),
    ])
    expect(useStore.getState().doc?.source).toBe('book/ch1.md')
    expect(useStore.getState().docError).toBe('')
  })
})

/**
 * HANDOFF-091: what a save does with a reviewer's words it could not write.
 *
 * Each test puts its assertion about the words first, so on the commit before
 * this package it fails on what the page did with them rather than on a
 * function that did not exist yet.
 */
describe('a refused wording, and words typed while a save is in flight', () => {
  const bad = (part: string): boolean =>
    useStore.getState().log.some(l => l.level === 'bad' && l.text.includes(part))
  const ch2 = (segments: Segment[]): DocResponse => ({ ...doc(segments), source: 'book/ch2.md' })
  const hold = (): { until: Promise<void>; release: () => void } => {
    let release: () => void = () => undefined
    const until = new Promise<void>(r => { release = r })
    return { until, release }
  }
  const refused = { applied: 0, unknown: [], stored: {}, conflicts: { s0001: { text: '她整夜未眠。', token: 'tok9' } } }

  it('keeps the words a conflict refused, marked with what is stored now', async () => {
    await openWith([segment({ id: 's0001', token: 'tok1' })])
    drafts.set('s0001', '她一夜沒睡。', '她沒有睡。')
    replies({ body: refused })
    otherwise({ body: doc([segment({ id: 's0001', target: '她整夜未眠。', token: 'tok9' })]) })

    const ok = await useStore.getState().save()
    expect(drafts.get('s0001')).toBe('她一夜沒睡。')
    expect(drafts.conflict('s0001')).toEqual({ text: '她整夜未眠。', token: 'tok9' })
    expect(ok).toBe(false)
    expect(bad('s0001 changed underneath this edit and was not written')).toBe(true)
    expect(bad('beside what is stored now')).toBe(true)
  })

  it('sends a refused wording again only once the reviewer chooses it, against what they were shown', async () => {
    await openWith([segment({ id: 's0001', token: 'tok1' })])
    drafts.set('s0001', '她一夜沒睡。', '她沒有睡。')
    replies({ body: refused })
    otherwise({ body: doc([segment({ id: 's0001', target: '她整夜未眠。', token: 'tok9' })]) })
    await useStore.getState().save()

    // More words, a blur, Ctrl+Enter: nothing goes out for this row.
    drafts.set('s0001', '她一夜沒睡。還', '她整夜未眠。')
    await useStore.getState().save()
    await useStore.getState().save()
    expect(callsTo('/api/save')).toHaveLength(1)
    expect(drafts.get('s0001')).toBe('她一夜沒睡。還')

    drafts.overrule('s0001')
    replies({ body: { applied: 1, unknown: [], stored: { s0001: { text: '她一夜沒睡。還', token: 'tok10' } }, conflicts: {} } })
    expect(await useStore.getState().save()).toBe(true)
    const sent = lastCall('/api/save')?.body as { targets: unknown; base: unknown }
    expect(sent.targets).toEqual({ s0001: '她一夜沒睡。還' })
    expect(sent.base).toEqual({ s0001: 'tok9' })
    expect(drafts.has('s0001')).toBe(false)
    expect(drafts.conflict('s0001')).toBeUndefined()
  })

  it('marks a chosen wording again when the stored one moved once more', async () => {
    await openWith([segment({ id: 's0001', token: 'tok1' })])
    drafts.set('s0001', '她一夜沒睡。', '她沒有睡。')
    replies(
      { body: refused },
      { body: doc([segment({ id: 's0001', target: '她整夜未眠。', token: 'tok9' })]) },
      { body: { applied: 0, unknown: [], stored: {}, conflicts: { s0001: { text: '她沒睡。', token: 'tok11' } } } },
    )
    otherwise({ body: doc([segment({ id: 's0001', target: '她沒睡。', token: 'tok11' })]) })
    await useStore.getState().save()
    drafts.overrule('s0001')
    expect(await useStore.getState().save()).toBe(false)
    expect(drafts.get('s0001')).toBe('她一夜沒睡。')
    expect(drafts.conflict('s0001')).toEqual({ text: '她沒睡。', token: 'tok11' })
  })

  it('drops the words when the reviewer chooses the stored wording', async () => {
    await openWith([segment({ id: 's0001', token: 'tok1' })])
    drafts.set('s0001', '她一夜沒睡。', '她沒有睡。')
    replies({ body: refused })
    otherwise({ body: doc([segment({ id: 's0001', target: '她整夜未眠。', token: 'tok9' })]) })
    await useStore.getState().save()
    drafts.discard('s0001')
    expect(drafts.has('s0001')).toBe(false)
    expect(drafts.conflict('s0001')).toBeUndefined()
    expect(await useStore.getState().save()).toBe(true)
    expect(callsTo('/api/save')).toHaveLength(1)
  })

  // The package's own probe, verbatim but for the fixture helpers.
  it('keeps text typed while the save that sent an older version was in flight', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', token: 't0' })])
    drafts.set('s0001', '燈還', 'orig')
    const wire = hold()
    replies({ body: { applied: 1, unknown: [], stored: {}, conflicts: {} }, after: wire.until })
    otherwise({ body: doc([segment({ id: 's0001', target: '燈還', token: 'w1' })]) })
    const saving = useStore.getState().save()
    drafts.set('s0001', '燈還亮著', 'orig')
    wire.release()
    await saving
    expect(drafts.get('s0001')).toBe('燈還亮著')
  })

  it('bases the next send of text typed during a save on the token that save produced', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', token: 't0' })])
    drafts.set('s0001', '燈還', 'orig')
    const wire = hold()
    replies(
      { body: { applied: 1, unknown: [], stored: { s0001: { text: '燈還', token: 'w1' } }, conflicts: {} }, after: wire.until },
      // The re-read is older than the write — a read ordering is HANDOFF-096's.
      { body: doc([segment({ id: 's0001', target: 'orig', token: 't0' })]) },
    )
    otherwise({ body: { applied: 1, unknown: [], stored: { s0001: { text: '燈還亮著', token: 'w2' } }, conflicts: {} } })
    const saving = useStore.getState().save()
    drafts.set('s0001', '燈還亮著', 'orig')
    wire.release()
    await saving
    await useStore.getState().save()
    const sent = lastCall('/api/save')?.body as { targets: unknown; base: unknown }
    expect(sent.targets).toEqual({ s0001: '燈還亮著' })
    expect(sent.base).toEqual({ s0001: 'w1' })
  })

  it('writes words typed during the flush on the way out, and leaves', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', token: 't0' })])
    drafts.set('s0001', '燈還', 'orig')
    const wire = hold()
    let n = 0
    answering(call => {
      if (call.path.startsWith('/api/save')) {
        n += 1
        return n === 1
          ? { body: { applied: 1, unknown: [], stored: { s0001: { text: '燈還', token: 'w1' } }, conflicts: {} }, after: wire.until }
          : { body: { applied: 1, unknown: [], stored: { s0001: { text: '燈還亮著', token: 'w2' } }, conflicts: {} } }
      }
      if (call.path.startsWith('/api/doc')) return { body: ch2([segment({ id: 's0001' })]) }
      return null
    })
    const opening = useStore.getState().open('book/ch2.md', 'zh-TW')
    drafts.set('s0001', '燈還亮著', 'orig')
    wire.release()
    await opening
    expect(useStore.getState().docError).toBe('')
    expect(useStore.getState().doc?.source).toBe('book/ch2.md')
    const saves = callsTo('/api/save')
    expect(saves).toHaveLength(2)
    expect((saves[1]!.body as { targets: unknown }).targets).toEqual({ s0001: '燈還亮著' })
    expect((saves[1]!.body as { base: unknown }).base).toEqual({ s0001: 'w1' })
  })

  it('says nothing failed when typing outlasts both laps of the flush', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', token: 't0' })])
    drafts.set('s0001', '燈', 'orig')
    const first = hold()
    const second = hold()
    let n = 0
    answering(call => {
      if (call.path.startsWith('/api/save')) {
        n += 1
        const text = (call.body as { targets: Record<string, string> }).targets.s0001!
        return { body: { applied: 1, unknown: [], stored: { s0001: { text, token: `w${n}` } }, conflicts: {} },
          after: n === 1 ? first.until : second.until }
      }
      return { body: ch2([segment({ id: 's0001' })]) }
    })
    const opening = useStore.getState().open('book/ch2.md', 'zh-TW')
    drafts.set('s0001', '燈還', 'orig')
    first.release()
    await new Promise(r => { setTimeout(r, 20) })
    expect(callsTo('/api/save')).toHaveLength(2)
    drafts.set('s0001', '燈還亮著', 'orig')
    second.release()
    await opening
    const error = useStore.getState().docError
    expect(drafts.get('s0001')).toBe('燈還亮著')
    expect(error).toMatch(/^s0001 was typed into while book\/ch1\.md was being written/)
    expect(error).toContain('nothing failed')
    expect(error).not.toContain('could not be written')
    expect(useStore.getState().doc?.source).toBe('book/ch1.md')
  })

  it('does not leave a document while a refused wording waits there, and says why', async () => {
    await openWith([segment({ id: 's0001', token: 'tok1' })])
    drafts.set('s0001', '她一夜沒睡。', '她沒有睡。')
    replies({ body: refused })
    otherwise({ body: doc([segment({ id: 's0001', target: '她整夜未眠。', token: 'tok9' })]) })
    await useStore.getState().save()
    const reads = callsTo('/api/doc').length
    await useStore.getState().open('book/ch2.md', 'zh-TW')
    expect(drafts.get('s0001')).toBe('她一夜沒睡。')
    expect(useStore.getState().docError).toMatch(/^s0001 changed in book\/ch1\.md underneath your edit/)
    expect(useStore.getState().docError).not.toContain('could not be written')
    expect(useStore.getState().doc?.source).toBe('book/ch1.md')
    expect(callsTo('/api/doc')).toHaveLength(reads)
    expect(callsTo('/api/save')).toHaveLength(1)
  })

  it('takes a conflict carrying the very text sent as a write, and says nothing', async () => {
    await openWith([segment({ id: 's0001', token: 'tok1' })])
    drafts.set('s0001', '她一夜沒睡。', '她沒有睡。')
    replies({ body: { applied: 0, unknown: [], stored: {}, conflicts: { s0001: { text: '她一夜沒睡。', token: 'tok5' } } } })
    otherwise({ body: doc([segment({ id: 's0001', target: '她一夜沒睡。', token: 'tok5' })]) })
    expect(await useStore.getState().save()).toBe(true)
    expect(drafts.has('s0001')).toBe(false)
    expect(bad('changed underneath')).toBe(false)
  })

  it('sends one request for saves asked while one is on the wire', async () => {
    await openWith([segment({ id: 's0001', token: 'tok1' })])
    drafts.set('s0001', '她一夜沒睡。', '她沒有睡。')
    const wire = hold()
    replies({ body: { applied: 1, unknown: [], stored: { s0001: { text: '她一夜沒睡。', token: 'tok2' } }, conflicts: {} }, after: wire.until })
    otherwise({ body: doc([segment({ id: 's0001', target: '她一夜沒睡。', token: 'tok2' })]) })
    const all = [useStore.getState().save(), useStore.getState().save(), useStore.getState().save()]
    wire.release()
    expect(await Promise.all(all)).toEqual([true, true, true])
    expect(callsTo('/api/save')).toHaveLength(1)
    expect(bad('changed underneath')).toBe(false)
  })

  /**
   * **Known broken, HANDOFF-096 (its ninth case).** Found by HANDOFF-091's
   * premise review and left open there on purpose. `base` is read from `doc`
   * when a save is sent, not when the edit began, so a re-read landing between
   * the first keystroke and the save — `follow()`'s at the end of a run, or
   * another row's save — moves the base past wording the reviewer never saw,
   * and the save overwrites a batch's write with no conflict reported. The fix
   * is the token of the wording an edit began on, advanced by the page's own
   * writes, which is HANDOFF-096's to design. `it.fails`, so the suite turns red
   * the day it lands and the marker is removed in the same commit.
   */
  it.fails('bases an edit on the token of the wording it began on, not on a re-read that landed since', async () => {
    await openWith([segment({ id: 's0001', token: 'tok1' })])
    drafts.set('s0001', '她一夜沒睡。', '她沒有睡。')
    replies({ body: doc([segment({ id: 's0001', target: '批次寫下的句子。', token: 'tok7' })]) })
    await useStore.getState().refresh()
    replies({ body: { applied: 1, unknown: [], stored: {}, conflicts: {} } })
    otherwise({ body: doc([segment({ id: 's0001', target: '她一夜沒睡。', token: 'tok8' })]) })
    await useStore.getState().save()
    expect((lastCall('/api/save')?.body as { base: unknown }).base).toEqual({ s0001: 'tok1' })
  })

  /** A server that keeps a text and token per id and compare-and-swaps on `base`. */
  const casServer = (gates: Record<number, Promise<void>>): Record<string, { text: string; token: string }> => {
    const server: Record<string, { text: string; token: string }> = {
      s0001: { text: 'orig', token: 't0' }, s0002: { text: 'two', token: 'u0' },
    }
    let n = 0
    answering(call => {
      if (call.path.startsWith('/api/save')) {
        n += 1
        const { targets, base } = call.body as { targets: Record<string, string>; base: Record<string, string> }
        const stored: Record<string, { text: string; token: string }> = {}
        const conflicts: Record<string, { text: string; token: string }> = {}
        for (const [id, text] of Object.entries(targets)) {
          if (base[id] && base[id] !== server[id]!.token) { conflicts[id] = { ...server[id]! }; continue }
          server[id] = { text, token: `w${n}${id}` }
          stored[id] = { ...server[id]! }
        }
        return { body: { applied: Object.keys(stored).length, unknown: [], stored, conflicts }, after: gates[n] }
      }
      if (call.path.startsWith('/api/doc')) {
        if (call.path.includes('ch2')) return { body: ch2([segment({ id: 's0001' })]) }
        return { body: doc(Object.entries(server).map(([id, s]) => segment({ id, target: s.text, token: s.token }))) }
      }
      return null
    })
    return server
  }

  /**
   * **Known broken, HANDOFF-096 (its tenth case).** Found by the second review
   * of HANDOFF-091 and left there by the maintainer's ruling of 2026-11-02. While
   * `open()` flushes, the address has moved, so no lap is read back: the page
   * still holds the token from before lap 1's write, and lap 1 forgot the entry
   * it wrote with its base. Words typed into that row during lap 2 are then sent,
   * on the re-open the refusal recommends, against the old token, and come back
   * as a conflict with the page's own write. No words are lost — the row shows
   * both — but the refusal's "which writes it" is not true here. The same root
   * as case 9: the token of the page's own writes.
   */
  it.fails('writes words typed during the second lap on the re-open the refusal recommends, with no conflict against the page\'s own write', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', token: 't0' }), segment({ id: 's0002', target: 'two', token: 'u0' })])
    const lap1 = hold()
    const lap2 = hold()
    const server = casServer({ 1: lap1.until, 2: lap2.until })
    drafts.set('s0001', 'X', 'orig')
    const opening = useStore.getState().open('book/ch2.md', 'zh-TW')
    drafts.set('s0002', 'Y', 'two')
    lap1.release()
    await new Promise(r => { setTimeout(r, 20) })
    expect(callsTo('/api/save')).toHaveLength(2)
    drafts.set('s0001', 'XZ', 'orig')
    lap2.release()
    await opening
    expect(useStore.getState().docError).toMatch(/s0001 was typed into while/)
    await useStore.getState().open('book/ch1.md', 'zh-TW')
    expect(bad('changed underneath')).toBe(false)
    expect(server.s0001!.text).toBe('XZ')
  })

  /**
   * **Known broken, HANDOFF-096 (its eleventh case).** The same window: a field
   * typed back to the wording it showed before lap 1 drops its entry, because
   * the id stopped counting as in flight with no read-back, and the server keeps
   * lap 1's words. Older than HANDOFF-091 (it fails on `3789c5e` too).
   */
  it.fails('keeps a field typed back to its pre-save wording during the second lap', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', token: 't0' }), segment({ id: 's0002', target: 'two', token: 'u0' })])
    const lap1 = hold()
    const lap2 = hold()
    const server = casServer({ 1: lap1.until, 2: lap2.until })
    drafts.set('s0001', 'X', 'orig')
    const opening = useStore.getState().open('book/ch2.md', 'zh-TW')
    drafts.set('s0002', 'Y', 'two')
    lap1.release()
    await new Promise(r => { setTimeout(r, 20) })
    drafts.set('s0001', 'orig', 'orig')
    lap2.release()
    await opening
    expect(drafts.has('s0001') || server.s0001!.text === 'orig').toBe(true)
  })

  it('takes nothing as written from a reply it cannot read', async () => {
    await openWith([segment({ id: 's0001', token: 'tok1' })])
    drafts.set('s0001', '她一夜沒睡。', '她沒有睡。')
    replies({ body: {} })
    expect(await useStore.getState().save()).toBe(false)
    expect(drafts.get('s0001')).toBe('她一夜沒睡。')
    expect(bad('cannot read')).toBe(true)
  })

  // ── found by the correctness review of HANDOFF-091's first version ──────────

  // One save on the wire at a time made a request that never answers a lock:
  // no later save was sent and `open()`, which waits on its flush, never left.
  it('stops waiting for a save that never answers, keeps the words, and lets the next save and the next open through', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await openWith([segment({ id: 's0001', target: 'orig', token: 't0' })])
      drafts.set('s0001', '燈還亮著', 'orig')
      let n = 0
      answering(call => {
        if (call.path.startsWith('/api/save')) {
          n += 1
          return n === 1
            ? { body: {}, after: new Promise<void>(() => undefined) }
            : { body: { applied: 1, unknown: [], stored: { s0001: { text: '燈還亮著', token: 'w2' } }, conflicts: {} } }
        }
        if (call.path.startsWith('/api/doc')) return { body: ch2([segment({ id: 's0001' })]) }
        return null
      })
      let left = false
      const opening = useStore.getState().open('book/ch2.md', 'zh-TW').then(() => { left = true })
      await vi.advanceTimersByTimeAsync(SAVE_WAIT_MS - 1)
      expect(left).toBe(false)
      await vi.advanceTimersByTimeAsync(1)
      // Asserted rather than awaited, so that the page that waits forever fails
      // here instead of hanging the file.
      expect(left).toBe(true)
      await opening
      // It refused to leave over words it could not write, and said why.
      expect(useStore.getState().doc?.source).toBe('book/ch1.md')
      expect(drafts.get('s0001')).toBe('燈還亮著')
      expect(useStore.getState().docError).toMatch(/^s0001 could not be written to book\/ch1\.md/)
      expect(bad(`did not answer this save within ${SAVE_WAIT_MS / 1000} seconds`)).toBe(true)

      // The lock is gone: the next open sends the words again, and leaves.
      await useStore.getState().open('book/ch2.md', 'zh-TW')
      expect(callsTo('/api/save')).toHaveLength(2)
      expect(useStore.getState().doc?.source).toBe('book/ch2.md')
      expect(useStore.getState().docError).toBe('')
    } finally {
      vi.useRealTimers()
    }
  })

  // "Draft again" reads the segment's origin right after `save()` answers, to
  // decide whether to ask about replacing a person's wording. A save asked
  // while another was on the wire answered before that one's re-read landed.
  it('answers no save before the last round\'s re-read has landed, queued or asked after the reply', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', origin: 'llm:draft', token: 't0' })])
    drafts.set('s0001', '燈還亮著', 'orig')
    const wire = hold()
    const reread = hold()
    replies(
      { body: { applied: 1, unknown: [], stored: { s0001: { text: '燈還亮著', token: 'w1' } }, conflicts: {} }, after: wire.until },
      { body: doc([segment({ id: 's0001', target: '燈還亮著', origin: 'human', token: 'w1' })]), after: reread.until },
    )
    const answered: string[] = []
    void useStore.getState().save().then(() => { answered.push('first') })
    void useStore.getState().save().then(() => { answered.push('queued') })
    wire.release()
    await new Promise(r => { setTimeout(r, 20) })
    void useStore.getState().save().then(() => { answered.push('after the reply') })
    await new Promise(r => { setTimeout(r, 20) })
    expect(answered).toEqual([])
    expect(callsTo('/api/save')).toHaveLength(1)

    reread.release()
    await new Promise(r => { setTimeout(r, 20) })
    expect(answered).toHaveLength(3)
    // What the caller reads now is the document as the save left it.
    expect(useStore.getState().doc?.segments[0]?.origin).toBe('human')
  })

  // Between a reply and its re-read the row still shows the wording from before
  // the save; typing the field back to it used to end the entry while the
  // server held the newer words, with nothing left that would write the change.
  it('keeps a field typed back to its old wording while the save that changed it is still being read back', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', token: 't0' })])
    drafts.set('s0001', '燈還亮著', 'orig')
    const reread = hold()
    replies(
      { body: { applied: 1, unknown: [], stored: { s0001: { text: '燈還亮著', token: 'w1' } }, conflicts: {} } },
      { body: doc([segment({ id: 's0001', target: '燈還亮著', origin: 'human', token: 'w1' })]), after: reread.until },
    )
    const saving = useStore.getState().save()
    await new Promise(r => { setTimeout(r, 20) })
    expect(callsTo('/api/save')).toHaveLength(1)
    drafts.set('s0001', 'orig', 'orig')
    reread.release()
    await saving
    expect(drafts.get('s0001')).toBe('orig')

    replies({ body: { applied: 1, unknown: [], stored: { s0001: { text: 'orig', token: 'w2' } }, conflicts: {} } })
    otherwise({ body: doc([segment({ id: 's0001', target: 'orig', origin: 'human', token: 'w2' })]) })
    await useStore.getState().save()
    expect((lastCall('/api/save')?.body as { targets: unknown; base: unknown })).toMatchObject({
      targets: { s0001: 'orig' }, base: { s0001: 'w1' },
    })
  })

  // A reply from before the map was cleared used to end what the new parse had
  // in flight.
  it('lets no reply from a parse that has gone end what the parse on screen has in flight', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', token: 't0' })])
    drafts.set('s0001', '舊的字', 'orig')
    const old = hold()
    const fresh = hold()
    replies(
      { body: { applied: 1, unknown: [], stored: {}, conflicts: {} }, after: old.until },
      { body: { applied: 1, unknown: [], stored: { s0001: { text: '新的字', token: 'n1' } }, conflicts: {} }, after: fresh.until },
    )
    otherwise({ body: doc([segment({ id: 's0001', target: 'orig', token: 't0' })]) })
    const before = useStore.getState().save()
    // The page put another parse on screen: what `open()` does on a document change.
    drafts.clear()
    drafts.set('s0001', '新的字', 'orig')
    const now = useStore.getState().save()
    old.release()
    await before
    // The new parse's send is still in flight, so typing the field back keeps it.
    drafts.set('s0001', 'orig', 'orig')
    expect(drafts.get('s0001')).toBe('orig')
    fresh.release()
    await now
  })

  // ── found by the second review and the mutation run of HANDOFF-091 ──────────

  // The re-read after a save is waited on by every later save, so one that
  // never answers held them all, and `open()`, for good — the lock the POST's
  // bound had just removed, one request later. The reviewer's probe.
  it('stops waiting for a re-read that never answers, so later saves and the next open still answer', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await openWith([segment({ id: 's0001', target: 'orig', token: 't0' }), segment({ id: 's0002', target: 'two', token: 'u0' })])
      answering(call => {
        if (call.path.startsWith('/api/save')) {
          const t = (call.body as { targets: Record<string, string> }).targets
          const stored = Object.fromEntries(Object.entries(t).map(([k, v]) => [k, { text: v, token: 'w-' + k }]))
          return { body: { applied: Object.keys(t).length, unknown: [], stored, conflicts: {} } }
        }
        if (call.path.startsWith('/api/doc')) {
          if (call.path.includes('ch2')) return { body: ch2([segment({ id: 's0001' })]) }
          return { body: {}, after: new Promise<void>(() => undefined) }
        }
        return null
      })
      drafts.set('s0001', 'X', 'orig')
      void useStore.getState().save()
      await vi.advanceTimersByTimeAsync(10)
      expect(callsTo('/api/save')).toHaveLength(1)
      drafts.set('s0002', 'Y', 'two')
      let left = false
      void useStore.getState().open('book/ch2.md', 'zh-TW').then(() => { left = true })
      await vi.advanceTimersByTimeAsync(SAVE_WAIT_MS * 2)
      expect(callsTo('/api/save')).toHaveLength(2)
      expect(left).toBe(true)
      expect(useStore.getState().doc?.source).toBe('book/ch2.md')
      expect(useStore.getState().log.some(l => l.level === 'warn' && l.text.includes('was not read back within'))).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  // The bound answers the caller; it must not also end the in-flight count, or
  // a field typed back to the wording still shown loses the change the server
  // never got (found by the third review; the reviewer's probe).
  it('keeps an entry typed back to the shown wording after the re-read bound fired', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', token: 't0' })])
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      answering(call => {
        if (call.path.startsWith('/api/save')) {
          return { body: { applied: 1, unknown: [], stored: { s0001: { text: 'new', token: 'w1' } }, conflicts: {} } }
        }
        if (call.path.startsWith('/api/doc')) return { body: {}, after: new Promise<void>(() => undefined) }
        return null
      })
      drafts.set('s0001', 'new', 'orig')
      let answered = false
      void useStore.getState().save().then(() => { answered = true })
      await vi.advanceTimersByTimeAsync(SAVE_WAIT_MS + 10)
      expect(answered).toBe(true)
      expect(useStore.getState().doc?.segments[0]?.target).toBe('orig')
      drafts.set('s0001', 'orig', 'orig')
      expect(drafts.has('s0001')).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('aborts the request it stops waiting for', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await openWith([segment({ id: 's0001', target: 'orig', token: 't0' })])
      drafts.set('s0001', '燈還亮著', 'orig')
      replies({ body: {}, after: new Promise<void>(() => undefined) })
      void useStore.getState().save()
      await vi.advanceTimersByTimeAsync(10)
      const signal = callsTo('/api/save')[0]?.signal
      expect(signal?.aborted).toBe(false)
      await vi.advanceTimersByTimeAsync(SAVE_WAIT_MS)
      expect(signal?.aborted).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  // A reply about a parse that has gone may not mark, forget or settle an entry
  // of the parse on screen, which has the same ids for other paragraphs.
  it('lets no reply from a parse that has gone mark an entry of the parse on screen', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', token: 't0' })])
    drafts.set('s0001', '舊的字', 'orig')
    const old = hold()
    replies({ body: { applied: 0, unknown: [], stored: {}, conflicts: { s0001: { text: '別人的字', token: 'z' } } }, after: old.until })
    otherwise({ body: doc([segment({ id: 's0001', target: 'orig', token: 't0' })]) })
    const before = useStore.getState().save()
    drafts.clear()
    drafts.set('s0001', '新的字', 'orig')
    old.release()
    await before
    expect(drafts.conflict('s0001')).toBeUndefined()
    expect(drafts.get('s0001')).toBe('新的字')
  })

  // Two rounds may send one id while the first is still being read back; the
  // first to finish must not end the other's.
  it('keeps an id in flight until the last round that sent it has been read back', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', token: 't0' })])
    drafts.set('s0001', '一', 'orig')
    const reread = hold()
    const second = hold()
    replies(
      { body: { applied: 1, unknown: [], stored: { s0001: { text: '一', token: 'w1' } }, conflicts: {} } },
      { body: doc([segment({ id: 's0001', target: '一', token: 'w1' })]), after: reread.until },
      { body: { applied: 1, unknown: [], stored: { s0001: { text: '二', token: 'w2' } }, conflicts: {} }, after: second.until },
    )
    otherwise({ body: doc([segment({ id: 's0001', target: '二', token: 'w2' })]) })
    const a = useStore.getState().save()
    await new Promise(r => { setTimeout(r, 20) })
    drafts.set('s0001', '二', 'orig')
    const b = useStore.getState().save()
    await new Promise(r => { setTimeout(r, 20) })
    expect(callsTo('/api/save')).toHaveLength(2)
    reread.release()
    await new Promise(r => { setTimeout(r, 20) })
    // The first round is over; the second, which sent this id too, is not.
    drafts.set('s0001', 'orig', 'orig')
    expect(drafts.get('s0001')).toBe('orig')
    second.release()
    await Promise.all([a, b])
  })

  // The backstop alone: a conflict carrying the very text sent is a write even
  // when the field has moved on since, so the newer words stay unmarked.
  it('takes a conflict carrying the text sent as a write while newer words wait in the field', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', token: 't0' })])
    drafts.set('s0001', '一', 'orig')
    const wire = hold()
    replies({ body: { applied: 0, unknown: [], stored: {}, conflicts: { s0001: { text: '一', token: 'w1' } } }, after: wire.until })
    otherwise({ body: doc([segment({ id: 's0001', target: '一', token: 'w1' })]) })
    const saving = useStore.getState().save()
    drafts.set('s0001', '一二', 'orig')
    wire.release()
    await saving
    expect(drafts.conflict('s0001')).toBeUndefined()
    expect(drafts.get('s0001')).toBe('一二')
    expect(drafts.base('s0001')).toBe('w1')
  })

  // Convergence alone: the field typed to the stored wording while the refused
  // request was in flight is no conflict, and nothing waits on a choice.
  it('drops an entry that already says what is stored when its save is refused', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', token: 't0' })])
    drafts.set('s0001', '一', 'orig')
    const wire = hold()
    replies({ body: { applied: 0, unknown: [], stored: {}, conflicts: { s0001: { text: '別人的字', token: 'z' } } }, after: wire.until })
    otherwise({ body: doc([segment({ id: 's0001', target: '別人的字', token: 'z' })]) })
    const saving = useStore.getState().save()
    drafts.set('s0001', '別人的字', 'orig')
    wire.release()
    await saving
    expect(drafts.has('s0001')).toBe(false)
    expect(drafts.conflict('s0001')).toBeUndefined()
  })

  // "Write mine over it" names the token of the wording the reviewer was shown,
  // even when the page's own copy of the document says something older.
  it('sends a chosen wording against the token it was shown, not the one the page holds', async () => {
    await openWith([segment({ id: 's0001', target: 'orig', token: 'tok1' })])
    drafts.set('s0001', '她一夜沒睡。', 'orig')
    replies({ body: refused })
    // The re-read after the refusal fails, so the page still holds `tok1`.
    otherwise({ status: 500, body: { error: 'database is locked' } })
    await useStore.getState().save()
    expect(useStore.getState().doc?.segments[0]?.token).toBe('tok1')
    drafts.overrule('s0001')
    replies({ body: { applied: 1, unknown: [], stored: { s0001: { text: '她一夜沒睡。', token: 'tok10' } }, conflicts: {} } })
    await useStore.getState().save()
    expect((lastCall('/api/save')?.body as { base: unknown }).base).toEqual({ s0001: 'tok9' })
  })

  it('clears the notice when another document is put on screen, and when the run it names ends', async () => {
    await openWith([segment()])
    useStore.getState().setNotice('book/ch1.md was not re-extracted')
    replies({ body: ch2([segment()]) })
    await useStore.getState().open('book/ch2.md', 'zh-TW')
    expect(useStore.getState().notice).toBe('')

    useStore.getState().setNotice('book/ch2.md was not re-extracted')
    replies({ body: { id: 'j1', total: 0, route: { provider: 'local', model: 'qwen', error: null } } })
    await useStore.getState().runJob('draft')
    expect(useStore.getState().notice).toBe('')
  })
})

describe('what the registry forgets with an entry', () => {
  it('ends an in-flight count, a base and a mark with the parse they were kept in', () => {
    drafts.clear()
    drafts.set('s0001', '一', 'orig')
    drafts.sending(['s0001'])
    drafts.settle({ s0001: '舊' }, { s0001: 'w1' })
    expect(drafts.base('s0001')).toBe('w1')
    drafts.clear()
    drafts.set('s0001', '二', 'orig')
    expect(drafts.base('s0001')).toBeUndefined()
    // Not in flight in this parse: typing back to the shown wording ends it.
    drafts.set('s0001', 'orig', 'orig')
    expect(drafts.has('s0001')).toBe(false)
  })

  it('drops the base with the entry it was kept for', () => {
    drafts.clear()
    drafts.set('s0001', '一', 'orig')
    drafts.settle({ s0001: '舊' }, { s0001: 'w1' })
    drafts.discard('s0001')
    drafts.set('s0001', '二', 'orig')
    expect(drafts.base('s0001')).toBeUndefined()
    drafts.clear()
  })
})

/**
 * Where an unsaved edit may be destroyed at all.
 *
 * Every test above is about the *conditions* under which the discard fires,
 * and a set of conditions is an enumeration of today's callers: `open()` had
 * three when this was written, `HANDOFF-088` took one away rather than adding
 * one, and a test set written against today's goes quietly wrong on the day a
 * new one appears. This one is about *where* the destruction lives instead,
 * which is the half that survives a call site nobody has written yet.
 *
 * It is a text scan and it says so: it cannot tell a call inside `open()` from
 * one anywhere else in the same file. What it can say is that there is one.
 */
describe('where an unsaved edit may be destroyed', () => {
  // Read through Vite's own module graph rather than through `node:fs`: this
  // package's `types` deliberately carries no node declarations, and a scan
  // that made it carry them would let the application itself reach for node
  // APIs and still typecheck.
  const tree = import.meta.glob('./**/*.{ts,tsx}', {
    query: '?raw', import: 'default', eager: true,
  }) as Record<string, string>

  const production = Object.entries(tree).filter(([path]) =>
    !/\.test\.tsx?$/.test(path) && !path.startsWith('./test/') && path !== './drafts.ts')

  const sites = (call: string): [string, number][] =>
    production
      .map(([path, text]) => [path, text.split(call).length - 1] as [string, number])
      .filter(([, n]) => n > 0)

  it('empties the map in exactly one place, and marks it void in exactly one other', () => {
    expect(sites('drafts.clear(')).toEqual([['./store.ts', 1]])
    expect(sites('drafts.strand(')).toEqual([['./store.ts', 1]])
  })

  // HANDOFF-091: a reviewer's refused wording is dropped, or written over the
  // stored one, only by the reviewer's own choice in its row.
  it('drops a refused wording, or writes it anyway, from the row alone', () => {
    expect(sites('drafts.discard(')).toEqual([['./components/SegmentRow.tsx', 1]])
    expect(sites('drafts.overrule(')).toEqual([['./components/SegmentRow.tsx', 1]])
  })
})

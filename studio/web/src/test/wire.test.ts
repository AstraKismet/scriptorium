/**
 * The stand-in for the wire keeps two promises of its own, and a test that
 * relies on a broken harness asserts about answers it never set up.
 *
 * Both were latent when an adversarial pass found them unpinned: nothing in the
 * suite yet runs a queue-driven test after one that answers by path, and none
 * mixes the two. Both are exactly what the next test to arrive would lean on.
 */
import { describe, expect, it } from 'vitest'

import { answering, install, replies } from './wire'

const answer = async (): Promise<unknown> => (await fetch('/api/anything')).json()

describe('the wire a test talks to', () => {
  it('forgets a previous test\'s answering function when it is installed again', async () => {
    answering(() => ({ body: 'left over' }))
    install()
    replies({ body: 'this test\'s own' })
    expect(await answer()).toBe('this test\'s own')
  })

  it('lets answering outrank the queue, which a whole-application test depends on', async () => {
    // The margin and the model list fetch on timers of their own, so a queued
    // answer meant for one request is taken by another unless the function
    // that answers by path is asked first.
    replies({ body: 'queued' })
    answering(() => ({ body: 'by path' }))
    expect(await answer()).toBe('by path')
  })
})

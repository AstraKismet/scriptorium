/**
 * The backend editor's key hint keeps its subject when the server masks the name.
 *
 * Since 2026-09-13 a hand-edited `api_key_env` that is not a variable name is
 * projected as `key_env: ""` beside `needs_key: true` and an `error` — the
 * server shows no value a field can never hold. Every sentence here that
 * interpolated `key_env` then lost its subject: "` NOT set`" in the list,
 * "` is not set in the environment…`" in the editor, and "`name —  not set`" in
 * the toolbar. The row's `error` explained it; a sentence without a subject is
 * still wrong. HANDOFF-080.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { Backends } from './Backends'
import { ModelPicker } from './ModelPicker'
import { useStore } from '../store'
import { otherwise } from '../test/wire'
import { CONTRACT_VERSION } from '../contract'
import type { Provider, StateResponse } from '../contract'

const initial = useStore.getState()

const masked: Provider = {
  name: 'hand', kind: 'openai', model: 'm', base_url: 'http://127.0.0.1:9/v1',
  needs_key: true, key_present: false, key_env: '',
  timeout: null, temperature: null, max_tokens: null, retries: null,
  error: 'providers.hand cannot be read: `api_key_env` is the NAME of an environment variable, as text. Fix it in lx.config.json.',
}

const named: Provider = {
  ...masked, name: 'openai', key_env: 'OPENAI_API_KEY', error: undefined,
}

const state: StateResponse = {
  contract_version: CONTRACT_VERSION,
  version: '0.4.0',
  cwd: '/books',
  targets: ['zh-TW'],
  providers: [masked, named],
  routing: { draft: { provider: 'openai', model: 'm' }, polish: { provider: 'openai', model: 'm' }, repair: { provider: 'openai', model: 'm' } },
  docs: [],
  untracked: [],
  collisions: [],
}

beforeEach(() => {
  useStore.setState({ ...initial, state }, true)
  otherwise({ body: { provider: 'hand', configured: 'm', models: [], error: null } })
})

describe('a masked key variable', () => {
  it('is named as unreadable in the list and in the editor, never as an empty name', async () => {
    render(<Backends />)
    expect(screen.getByText(/key variable unreadable/)).toBeTruthy()
    expect(screen.getByText(/OPENAI_API_KEY NOT set/)).toBeTruthy()
    expect(screen.queryByText(/·\s+NOT set/)).toBeNull()

    fireEvent.click(screen.getByText('hand'))
    const hint = await screen.findByText(/is not a variable name, so it is not shown/)
    expect(hint.textContent).toMatch(/Type the variable’s name here and save/)
    expect(screen.queryByText(/^\s+is not set in the environment/)).toBeNull()
  })

  it('is named as unreadable in the toolbar option, never as an empty name', () => {
    render(<ModelPicker />)
    const option = screen.getByText(/hand — key variable unreadable/) as HTMLOptionElement
    expect(option.disabled).toBe(true)
    expect(screen.getByText(/openai — OPENAI_API_KEY not set/)).toBeTruthy()
    expect(screen.queryByText(/—\s+not set/)).toBeNull()
  })
})

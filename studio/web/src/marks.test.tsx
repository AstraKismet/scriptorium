import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Marked, placeholders, sameSlots } from './marks'

describe('placeholders', () => {
  it('treats a ⟦n⟧ run as an atom, the way the sentence rule does', () => {
    expect(placeholders('⟦1⟧⟦2⟧ text ⟦10⟧')).toEqual(['⟦1⟧', '⟦2⟧', '⟦10⟧'])
  })

  it('compares them as a multiset, so a reordering is not a difference', () => {
    expect(sameSlots('⟦1⟧a⟦2⟧', '⟦2⟧b⟦1⟧')).toBe(true)
    expect(sameSlots('⟦1⟧a⟦2⟧', '⟦1⟧a')).toBe(false)
  })

  it('renders markup as nodes, never as a string of HTML', () => {
    // The server sends no Content-Security-Policy header — its header set is
    // itself part of the frozen contract — and a filename or a model id may
    // legally contain `<`, `>`, `"` and `'`. JSX escapes by construction, which
    // is the fix that cannot be short by one; the old page's own audit list was.
    const { container } = render(<Marked text={'<img src=x onerror=1> ⟦1⟧'} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toBe('<img src=x onerror=1> ⟦1⟧')
    expect(screen.getByText('⟦1⟧').className).toBe('ph')
  })
})

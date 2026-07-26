import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { StoresLedger } from '@/components/stores/StoresLedger'
import { api } from '@/lib/api'

// PULT-LAUNCH-1.4.5I §2 — the top-level action creates a CABINET. It must say «Добавить кабинет»,
// never «Добавить магазин» (which would misname creating a MarketplaceAccount).

beforeEach(() => { vi.restoreAllMocks() })

describe('Stores terminology', () => {
  it('empty state invites adding a cabinet, not a store', async () => {
    vi.spyOn(api.marketplaceAccounts, 'list').mockResolvedValue([])
    render(<StoresLedger />)

    expect(await screen.findByText(/Добавьте кабинет маркетплейса и выберите, как получать данные/)).toBeInTheDocument()
    const buttons = screen.getAllByRole('button').map(b => (b.textContent ?? '').trim())
    expect(buttons).toContain('Добавить кабинет')
    // the account-creating CTA is never mislabelled «Добавить магазин»
    expect(buttons).not.toContain('Добавить магазин')
  })
})

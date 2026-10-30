import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ConflictBanner } from '@/components/stores/Completeness'
import { StoreFinanceSummary } from '@/components/stores/StoreFinanceSummary'
import { api, type StoreFinanceSummaryOut } from '@/lib/api'

beforeEach(() => { vi.restoreAllMocks() })
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}
const conflict: StoreFinanceSummaryOut = {
  store_id: 'copy-store', revenue: 100, net_profit: 30, unassigned_revenue: 0,
  source: 'csv', completeness: 'complete', missing_fields: [], conflict: true,
  conflict_candidates: { api: 250, csv: 100 },
}
const resolved: StoreFinanceSummaryOut = {
  ...conflict, source: 'api', revenue: 250, net_profit: null, completeness: 'incomplete',
  missing_fields: ['cogs'], conflict: false, conflict_candidates: null,
}
const ack = { metric_type: 'revenue', preference: 'api' as const, api_supported: true, api_available: true, limitation: null }

describe('conflict copy follows confirmed source', () => {
  // The endpoint currently emits conflicts only with safe CSV. API here is a
  // defensive component-contract test, not a claim that the endpoint emits it.
  it.each(['csv', 'api'] as const)('describes the supplied %s source consistently without inventing a decision', source => {
    const choose = vi.fn()
    render(<ConflictBanner metricLabel="Выручка" period="2026-07" apiValue="250 ₽" csvValue="100 ₽" chosen={source} onChoose={choose} />)
    const banner = screen.getByRole('alert')
    const label = source.toUpperCase()
    expect(within(banner).getByText(new RegExp(`В расчёте используется ${label}\\.`))).toBeInTheDocument()
    expect(within(banner).getByText(`Сейчас: ${label}`)).toBeInTheDocument()
    expect(banner).not.toHaveTextContent('До вашего решения')
    expect(within(banner).getByText('250 ₽')).toBeInTheDocument()
    expect(within(banner).getByText('100 ₽')).toBeInTheDocument()
    expect(banner).not.toHaveTextContent('350 ₽')
    expect(choose).not.toHaveBeenCalled()
  })

  it('keeps a busy standalone banner on its last confirmed source and disables both choices', async () => {
    const choose = vi.fn()
    render(<ConflictBanner metricLabel="Выручка" apiValue="250 ₽" csvValue="100 ₽" chosen="csv" busy onChoose={choose} />)
    expect(screen.getByText(/В расчёте используется CSV\./)).toBeInTheDocument()
    for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Использовать API' }))
    expect(choose).not.toHaveBeenCalled()
  })

  it('hides the conflict while PATCH and reread are pending, then removes it after confirmed API resolution', async () => {
    const patch = deferred<typeof ack>()
    const read = deferred<StoreFinanceSummaryOut>()
    vi.spyOn(api.marketplaceStores, 'financeSummary').mockResolvedValueOnce(conflict).mockReturnValueOnce(read.promise)
    const write = vi.spyOn(api.sourcePolicy, 'set').mockReturnValue(patch.promise)
    render(<StoreFinanceSummary storeId="copy-store" />)
    const user = userEvent.setup()
    expect(await screen.findByText(/В расчёте используется CSV\./)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Использовать API' }))
    expect(screen.getByRole('status')).toHaveTextContent('Загружаем финансовый итог')
    expect(screen.queryByText(/Есть расхождение API и CSV/)).not.toBeInTheDocument()
    await act(async () => { patch.resolve(ack) })
    expect(screen.queryByText(/Источник финансового расчёта магазина:/)).not.toBeInTheDocument()
    await act(async () => { read.resolve(resolved) })
    expect(await screen.findByText(/Источник финансового расчёта магазина: API/)).toBeInTheDocument()
    expect(screen.queryByText(/Есть расхождение API и CSV/)).not.toBeInTheDocument()
    expect(screen.queryByText(/используется CSV/)).not.toBeInTheDocument()
    expect(write).toHaveBeenCalledTimes(1)
  })

  it('does not show a source banner on failed reread and retries GET without another PATCH', async () => {
    const read = vi.spyOn(api.marketplaceStores, 'financeSummary').mockResolvedValueOnce(conflict)
      .mockRejectedValueOnce(new Error('private response')).mockResolvedValueOnce(resolved)
    const write = vi.spyOn(api.sourcePolicy, 'set').mockResolvedValue(ack)
    render(<StoreFinanceSummary storeId="copy-store" />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Использовать API' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Выбор сохранён, но')
    expect(screen.queryByText(/Есть расхождение API и CSV/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Сейчас:/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Повторить' }))
    expect(await screen.findByText(/Источник финансового расчёта магазина: API/)).toBeInTheDocument()
    expect(read).toHaveBeenCalledTimes(3)
    expect(write).toHaveBeenCalledTimes(1)
    expect(document.body.textContent).not.toContain('private response')
  })

  it('does not declare the old CSV choice after an uncertain PATCH whose GET confirms API', async () => {
    vi.spyOn(api.marketplaceStores, 'financeSummary').mockResolvedValueOnce(conflict).mockResolvedValueOnce(resolved)
    const write = vi.spyOn(api.sourcePolicy, 'set').mockRejectedValue(new Error('lost reply'))
    render(<StoreFinanceSummary storeId="copy-store" />)
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Использовать API' }))
    expect(await screen.findByText(/Источник финансового расчёта магазина: API/)).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Не удалось подтвердить сохранение по ответу сервера')
    expect(screen.queryByText(/Сейчас: CSV|используется CSV/)).not.toBeInTheDocument()
    expect(write).toHaveBeenCalledTimes(1)
  })

  it('shows neither a conflict nor a guessed source when the initial GET fails', async () => {
    vi.spyOn(api.marketplaceStores, 'financeSummary').mockRejectedValue(new Error('private response'))
    const write = vi.spyOn(api.sourcePolicy, 'set')
    render(<StoreFinanceSummary storeId="copy-store" />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument())
    expect(screen.queryByText(/Сейчас:|используется CSV|используется API/)).not.toBeInTheDocument()
    expect(write).not.toHaveBeenCalled()
  })
})

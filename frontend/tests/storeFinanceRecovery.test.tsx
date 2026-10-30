import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import StorePage from '@/app/dashboard/stores/[storeId]/page'
import { StoreFinanceSummary } from '@/components/stores/StoreFinanceSummary'
import { SourcePolicySection } from '@/components/stores/SourcePolicySection'
import { api, type SourcePolicyOut, type StoreFinanceSummaryOut } from '@/lib/api'

const summary = (source: 'api' | 'csv' = 'csv'): StoreFinanceSummaryOut => ({
  store_id: 'st-1', revenue: source === 'api' ? 250 : 100, net_profit: null,
  source, completeness: 'incomplete', missing_fields: ['cogs'], unassigned_revenue: 0,
  conflict: true, conflict_candidates: { api: 250, csv: 100 },
})
const policy = (preference: 'api' | 'csv' = 'csv'): SourcePolicyOut => ({
  store_id: 'st-1', marketplace: 'wildberries',
  metrics: ['revenue', 'price'].map(metric_type => ({
    metric_type, preference, api_supported: true, api_available: true, limitation: null,
  })),
})
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}
const networkGuard = globalThis.fetch
beforeEach(() => { vi.restoreAllMocks() })
afterEach(() => { globalThis.fetch = networkGuard })

describe.each(['finance', 'policy'] as const)('%s read recovery', surface => {
  function mount() {
    return render(surface === 'finance'
      ? <StoreFinanceSummary storeId="st-1" />
      : <SourcePolicySection storeId="st-1" marketplace="wildberries" />)
  }
  function mockRead() {
    // Both return types are intentionally selected outside the shared spy.
    if (surface === 'finance') return vi.spyOn(api.marketplaceStores, 'financeSummary')
    return vi.spyOn(api.sourcePolicy, 'get')
  }
  const response = () => surface === 'finance' ? summary() : policy()
  const choose = () => surface === 'finance'
    ? screen.getByRole('button', { name: 'Использовать API' })
    : within(screen.getByRole('radiogroup', { name: 'Выручка' })).getByRole('radio', { name: 'Только API' })

  it('retries a failed initial GET without writing and hides stale content while pending', async () => {
    const read = mockRead()
    const next = deferred<StoreFinanceSummaryOut | SourcePolicyOut>()
    read.mockRejectedValueOnce(new Error('private failure')).mockImplementationOnce(() => next.promise as never)
    const write = vi.spyOn(api.sourcePolicy, 'set')
    mount()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Повторить' }))
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument()
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument()
    await act(async () => { next.resolve(response()) })
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
    expect(read).toHaveBeenCalledTimes(2)
    expect(write).not.toHaveBeenCalled()
    expect(document.body.textContent).not.toContain('private failure')
  })

  it('retries only GET when PATCH succeeded but the subsequent read failed', async () => {
    const read = mockRead()
    read.mockResolvedValueOnce(response() as never).mockRejectedValueOnce(new Error('read failed'))
      .mockResolvedValueOnce(response() as never)
    const write = vi.spyOn(api.sourcePolicy, 'set').mockResolvedValue(policy('api').metrics[0])
    mount()
    const user = userEvent.setup()
    await waitFor(() => expect(choose()).toBeInTheDocument())
    await user.click(choose())
    expect(await screen.findByText(/Выбор сохранён, но/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Повторить' }))
    await waitFor(() => expect(read).toHaveBeenCalledTimes(3))
    expect(write).toHaveBeenCalledTimes(1)
  })

  it('reconciles an uncertain PATCH through GET without claiming it was rejected', async () => {
    const read = mockRead()
    read.mockResolvedValueOnce(response() as never).mockRejectedValueOnce(new Error('read failed'))
      .mockResolvedValueOnce(response() as never)
    const write = vi.spyOn(api.sourcePolicy, 'set').mockRejectedValue(new Error('lost response'))
    mount()
    const user = userEvent.setup()
    await waitFor(() => expect(choose()).toBeInTheDocument())
    await user.click(choose())
    expect(await screen.findByText(/Не удалось подтвердить сохранение/)).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Повторить' }))
    await waitFor(() => expect(read).toHaveBeenCalledTimes(3))
    expect(write).toHaveBeenCalledTimes(1)
    expect(document.body.textContent).not.toContain('lost response')
  })
})

function mountPage() {
  const store = { id: 'st-1', label: 'Synthetic store', marketplace: 'wildberries', status: 'active' }
  vi.spyOn(api.marketplaceStores, 'imports').mockResolvedValue({ store, items: [], page: 1, page_size: 25, total: 0, pages: 0 })
  vi.spyOn(api.marketplaceStores, 'products').mockResolvedValue({ store, items: [], page: 1, page_size: 25, total: 0, pages: 0 })
  return render(<StorePage />)
}

it('refreshes finance after a revenue source change in the lower section', async () => {
  const read = vi.spyOn(api.marketplaceStores, 'financeSummary').mockResolvedValueOnce(summary()).mockResolvedValue(summary('api'))
  vi.spyOn(api.sourcePolicy, 'get').mockResolvedValueOnce(policy()).mockResolvedValue(policy('api'))
  const write = vi.spyOn(api.sourcePolicy, 'set').mockResolvedValue(policy('api').metrics[0])
  mountPage()
  const user = userEvent.setup()
  const group = await screen.findByRole('radiogroup', { name: 'Выручка' })
  await user.click(within(group).getByRole('radio', { name: 'Только API' }))
  expect(await screen.findByText(/Источник финансового расчёта магазина: API/)).toBeInTheDocument()
  expect(read).toHaveBeenCalledTimes(2)
  expect(write).toHaveBeenCalledTimes(1)
})

it('refreshes the lower policy after resolving a conflict above', async () => {
  vi.spyOn(api.marketplaceStores, 'financeSummary').mockResolvedValueOnce(summary()).mockResolvedValue(summary('api'))
  const read = vi.spyOn(api.sourcePolicy, 'get').mockResolvedValueOnce(policy()).mockResolvedValue(policy('api'))
  vi.spyOn(api.sourcePolicy, 'set').mockResolvedValue(policy('api').metrics[0])
  mountPage()
  const user = userEvent.setup()
  await screen.findByRole('radiogroup', { name: 'Выручка' })
  await user.click(await screen.findByRole('button', { name: 'Использовать API' }))
  await waitFor(() => expect(within(screen.getByRole('radiogroup', { name: 'Выручка' }))
    .getByRole('radio', { name: 'Только API', checked: true })).toBeInTheDocument())
  expect(read).toHaveBeenCalledTimes(2)
})

it('does not let a second row or the finance banner issue a parallel PATCH', async () => {
  vi.spyOn(api.marketplaceStores, 'financeSummary').mockResolvedValue(summary())
  vi.spyOn(api.sourcePolicy, 'get').mockResolvedValue(policy())
  const pending = deferred<ReturnType<typeof policy>['metrics'][number]>()
  const write = vi.spyOn(api.sourcePolicy, 'set').mockReturnValue(pending.promise)
  mountPage()
  const user = userEvent.setup()
  const revenue = await screen.findByRole('radiogroup', { name: 'Выручка' })
  await user.click(within(revenue).getByRole('radio', { name: 'Только API' }))
  expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Использовать CSV' })).not.toBeInTheDocument()
  expect(screen.getAllByRole('status')).toHaveLength(2)
  expect(write).toHaveBeenCalledTimes(1)
  await act(async () => { pending.resolve(policy('api').metrics[0]) })
})

it('retains a pending write lock across navigation and reconciles the remounted page', async () => {
  const read = vi.spyOn(api.marketplaceStores, 'financeSummary').mockResolvedValueOnce(summary()).mockResolvedValue(summary('api'))
  vi.spyOn(api.sourcePolicy, 'get').mockResolvedValueOnce(policy()).mockResolvedValue(policy('api'))
  const pending = deferred<ReturnType<typeof policy>['metrics'][number]>()
  const write = vi.spyOn(api.sourcePolicy, 'set').mockReturnValue(pending.promise)
  const first = mountPage()
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'Использовать API' }))
  first.unmount()
  mountPage()
  await screen.findByRole('heading', { name: 'Финансовый итог' })
  expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Использовать API' })).not.toBeInTheDocument()
  expect(read).toHaveBeenCalledTimes(1)
  await act(async () => { pending.resolve(policy('api').metrics[0]) })
  expect(await screen.findByText(/Источник финансового расчёта магазина: API/)).toBeInTheDocument()
  expect(within(await screen.findByRole('radiogroup', { name: 'Выручка' }))
    .getByRole('radio', { name: 'Только API', checked: true })).toBeInTheDocument()
  expect(write).toHaveBeenCalledTimes(1)
})

it.each([
  ['finance', false], ['policy', false], ['finance', true], ['policy', true],
] as const)('waits for a pre-write %s GET before a fresh snapshot; remount=%s (real transport dedup)', async (target, remount) => {
  const old = deferred<Response>()
  let financeReads = 0
  let policyReads = 0
  let writes = 0
  const unexpected: string[] = []
  const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 })
  globalThis.fetch = vi.fn(async (input, init) => {
    const path = new URL(String(input)).pathname
    if (init?.method === 'PATCH' && path === '/api/marketplace-stores/st-1/source-policy/revenue') {
      writes += 1
      return json(policy('api').metrics[0])
    }
    if (path === '/api/marketplace-stores/st-1/finance-summary') {
      financeReads += 1
      if (target === 'finance' && financeReads === 1) return old.promise
      return json(summary(writes ? 'api' : 'csv'))
    }
    if (path === '/api/marketplace-stores/st-1/source-policy') {
      policyReads += 1
      if (target === 'policy' && policyReads === 1) return old.promise
      return json(policy(writes ? 'api' : 'csv'))
    }
    unexpected.push(path)
    return new Response('{}', { status: 403 })
  })
  const first = mountPage()
  const user = userEvent.setup()
  if (remount) {
    if (target === 'finance') await screen.findByRole('radiogroup', { name: 'Выручка' })
    else await screen.findByRole('button', { name: 'Использовать API' })
    first.unmount()
    mountPage()
  }
  if (target === 'finance') {
    const group = await screen.findByRole('radiogroup', { name: 'Выручка' })
    await user.click(within(group).getByRole('radio', { name: 'Только API' }))
  } else {
    await user.click(await screen.findByRole('button', { name: 'Использовать API' }))
  }
  await waitFor(() => expect(writes).toBe(1))
  expect(target === 'finance' ? financeReads : policyReads).toBe(1)
  await act(async () => { old.resolve(json(target === 'finance' ? summary() : policy())) })
  await waitFor(() => expect(target === 'finance' ? financeReads : policyReads).toBe(2))
  expect(await screen.findByText(/Источник финансового расчёта магазина: API/)).toBeInTheDocument()
  await waitFor(() => expect(within(screen.getByRole('radiogroup', { name: 'Выручка' }))
    .getByRole('radio', { name: 'Только API', checked: true })).toBeInTheDocument())
  expect(writes).toBe(1)
  expect(unexpected).toEqual([])
})

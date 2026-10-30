import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConnectionsSection } from '@/components/connections/ConnectionsSection'
import { api, type MarketplaceConnectionOut } from '@/lib/api'

// Same connection fixture as connections.test.tsx.
const conn: MarketplaceConnectionOut = {
  id: 'c1', marketplace: 'wildberries', label: null, status: 'connected',
  verification_status: 'unverified', scopes: ['feedbacks'],
  scopes_verification: [{ scope: 'feedbacks', verification_status: 'unverified', verified_at: null }],
  created_at: '2026-01-01T00:00:00Z',
}
const retry = () => screen.getByRole('button', { name: 'Повторить загрузку подключений' })
const noEmpty = () => expect(screen.queryByText(/Пока ни один магазин не подключён/)).not.toBeInTheDocument()
const noMutations = () => {
  expect(api.connections.verify).not.toHaveBeenCalled()
  expect(api.connections.remove).not.toHaveBeenCalled()
  expect(api.connections.create).not.toHaveBeenCalled()
}
beforeEach(() => {
  vi.spyOn(api.connections, 'list').mockRejectedValue(new Error('HTTP 500'))
  vi.spyOn(api.connections, 'verify').mockResolvedValue({ outcome: 'verified' } as never)
  vi.spyOn(api.connections, 'remove').mockResolvedValue(undefined as never)
  vi.spyOn(api.connections, 'create').mockResolvedValue(conn)
})
afterEach(() => vi.restoreAllMocks())

describe('connection list read recovery', () => {
  it('coalesces two card actions into a serial follow-up GET without losing the final reload', async () => {
    const second = { ...conn, id: 'c2', marketplace: 'ozon' } as MarketplaceConnectionOut
    let resolveFirst!: (value: MarketplaceConnectionOut[]) => void
    let resolveFinal!: (value: MarketplaceConnectionOut[]) => void
    vi.mocked(api.connections.list)
      .mockResolvedValueOnce([conn, second])
      .mockReturnValueOnce(new Promise(r => { resolveFirst = r }))
      .mockReturnValueOnce(new Promise(r => { resolveFinal = r }))
    render(<ConnectionsSection />)
    const checks = await screen.findAllByRole('button', { name: 'Повторить проверку' })
    fireEvent.click(checks[0])
    await waitFor(() => expect(api.connections.list).toHaveBeenCalledTimes(2))
    fireEvent.click(checks[1])
    await waitFor(() => expect(api.connections.verify).toHaveBeenCalledTimes(2))
    await act(async () => {})
    expect(api.connections.list).toHaveBeenCalledTimes(2)
    await act(async () => resolveFirst([conn, second]))
    expect(api.connections.list).toHaveBeenCalledTimes(3)
    // Neither action finishes its reload before the queued snapshot is available.
    expect(screen.getAllByRole('button', { name: 'Повторить проверку' }).every(b => (b as HTMLButtonElement).disabled)).toBe(true)
    await act(async () => resolveFinal([conn, { ...second, status: 'revoked' }]))
    expect(screen.getByText('Отключён')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Повторить проверку' })).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Повторить проверку' })).toBeEnabled()
    await act(async () => {})
    expect(api.connections.list).toHaveBeenCalledTimes(3)
    expect(api.connections.remove).not.toHaveBeenCalled()
    expect(api.connections.create).not.toHaveBeenCalled()
  })

  it('keeps an initial pending read unknown, not empty', async () => {
    let resolve!: (value: MarketplaceConnectionOut[]) => void
    vi.mocked(api.connections.list).mockReturnValue(new Promise(r => { resolve = r }))
    render(<ConnectionsSection />)
    expect(screen.getByText('Загрузка подключений…')).toBeInTheDocument()
    noEmpty()
    await act(async () => resolve([]))
    expect(screen.getByText(/Пока ни один магазин не подключён/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    noMutations()
  })

  it('shows a persistent accessible failure instead of healthy empty', async () => {
    render(<ConnectionsSection />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось выполнить действие')
    noEmpty()
    expect(retry()).toBeEnabled()
    expect(screen.queryByRole('button', { name: /Скрыть|Закрыть/ })).not.toBeInTheDocument()
    noMutations()
  })

  for (const populated of [false, true]) {
    it(`GET retry recovers to ${populated ? 'populated' : 'success-empty'} without mutations`, async () => {
      render(<ConnectionsSection />)
      await screen.findByText(/Не удалось выполнить действие/)
      vi.mocked(api.connections.list).mockResolvedValue(populated ? [conn] : [])
      fireEvent.click(retry())
      if (populated) {
        await screen.findByRole('button', { name: 'Повторить проверку' })
        noEmpty()
      } else await screen.findByText(/Пока ни один магазин не подключён/)
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(api.connections.list).toHaveBeenCalledTimes(2)
      noMutations()
    })
  }

  it('disables pending retry, prevents duplicate reads, then keeps repeated failure recoverable', async () => {
    render(<ConnectionsSection />)
    await screen.findByText(/Не удалось выполнить действие/)
    let reject!: (error: Error) => void
    vi.mocked(api.connections.list).mockReturnValue(new Promise((_, r) => { reject = r }))
    fireEvent.click(retry())
    expect(retry()).toBeDisabled()
    fireEvent.click(retry())
    noEmpty()
    expect(screen.getByText('Загрузка подключений…')).toBeInTheDocument()
    expect(api.connections.list).toHaveBeenCalledTimes(2)
    await act(async () => reject(new Error('HTTP 503')))
    expect(screen.getByRole('alert')).toHaveTextContent('Не удалось выполнить действие')
    expect(retry()).toBeEnabled()
    noEmpty()
    noMutations()
  })

  it('preserves explicit verification and its onChanged GET reload', async () => {
    vi.mocked(api.connections.list).mockResolvedValue([conn])
    render(<ConnectionsSection />)
    fireEvent.click(await screen.findByRole('button', { name: 'Повторить проверку' }))
    await waitFor(() => expect(api.connections.list).toHaveBeenCalledTimes(2))
    expect(api.connections.verify).toHaveBeenCalledWith('c1', 'feedbacks')
    expect(await screen.findByText(/Ключ проверен/)).toBeInTheDocument()
    expect(api.connections.remove).not.toHaveBeenCalled()
  })
})

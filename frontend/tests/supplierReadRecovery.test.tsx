import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import SupplierVerifyPage from '@/app/dashboard/suppliers/verify/page'
import { api, type SupplierVerification } from '@/lib/api'

const entry = (status: SupplierVerification['status'] = 'pending'): SupplierVerification => ({
  id: 'sv-1', user_id: 'synthetic', company_name: 'Synthetic manufacturer', country: 'russia',
  inn: null, ogrn: null, legal_address: null, phone: null, website: null, uscc: null,
  business_scope: null, founded_year: null, status, verification_source: null,
  rejection_reason: null, verified_at: null, created_at: '2026-10-01T00:00:00Z',
})
const ownError = 'Не удалось загрузить ваши заявки.'
const registryError = 'Не удалось загрузить реестр производителей.'
const emptyRegistry = 'Верифицированных производителей пока нет'

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api.suppliers, 'my').mockResolvedValue([])
  vi.spyOn(api.suppliers, 'listVerified').mockResolvedValue([])
  vi.spyOn(api.suppliers, 'submit').mockRejectedValue(new Error('Mutation forbidden in read tests'))
  vi.spyOn(api.suppliers, 'revoke').mockRejectedValue(new Error('Mutation forbidden in read tests'))
})

it('does not enable submission when the active application state is unknown', async () => {
  vi.mocked(api.suppliers.my).mockRejectedValue(new Error('offline'))
  render(<SupplierVerifyPage />)
  expect(await screen.findByText(ownError)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Подать заявку на верификацию' })).not.toBeInTheDocument()
  expect(screen.getByText(ownError).closest('[role="alert"]')).not.toBeNull()
  expect(api.suppliers.submit).not.toHaveBeenCalled()
})

it('does not describe a failed registry read as an empty registry', async () => {
  vi.mocked(api.suppliers.listVerified).mockRejectedValue(new Error('offline'))
  render(<SupplierVerifyPage />)
  expect(await screen.findByText(registryError)).toBeInTheDocument()
  expect(screen.queryByText(emptyRegistry)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Подать заявку на верификацию' })).toBeInTheDocument()
})

it('keeps simultaneous read failures independent', async () => {
  vi.mocked(api.suppliers.my).mockRejectedValue(new Error('offline'))
  vi.mocked(api.suppliers.listVerified).mockRejectedValue(new Error('offline'))
  render(<SupplierVerifyPage />)
  expect(await screen.findByText(ownError)).toBeInTheDocument()
  expect(await screen.findByText(registryError)).toBeInTheDocument()
  expect(screen.getAllByRole('alert')).toHaveLength(2)
})

it('retries only the application read and respects an active application after recovery', async () => {
  vi.mocked(api.suppliers.my).mockRejectedValueOnce(new Error('offline')).mockResolvedValue([entry()])
  const user = userEvent.setup()
  render(<SupplierVerifyPage />)
  await user.click(await screen.findByRole('button', { name: 'Повторить загрузку заявок' }))
  expect(await screen.findByText('Мои заявки (1)')).toBeInTheDocument()
  expect(screen.queryByText(ownError)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Подать заявку на верификацию' })).not.toBeInTheDocument()
  expect(api.suppliers.my).toHaveBeenCalledTimes(2)
  expect(api.suppliers.listVerified).toHaveBeenCalledTimes(1)
  expect(api.suppliers.revoke).not.toHaveBeenCalled()
})

it.each([{ result: [] }, { result: [entry('verified')] }])('registry retry renders a genuine result without repeating own reads', async ({ result }) => {
  vi.mocked(api.suppliers.listVerified).mockRejectedValueOnce(new Error('offline')).mockResolvedValue(result)
  const user = userEvent.setup()
  render(<SupplierVerifyPage />)
  await user.click(await screen.findByRole('button', { name: 'Повторить загрузку реестра' }))
  expect(await screen.findByText(result.length ? 'Synthetic manufacturer' : emptyRegistry)).toBeInTheDocument()
  expect(screen.queryByText(registryError)).not.toBeInTheDocument()
  expect(api.suppliers.my).toHaveBeenCalledTimes(1)
  expect(api.suppliers.listVerified).toHaveBeenCalledTimes(2)
})

it('keeps submission hidden while a retry is pending', async () => {
  let resolve!: (value: SupplierVerification[]) => void
  const pending = new Promise<SupplierVerification[]>(done => { resolve = done })
  vi.mocked(api.suppliers.my).mockRejectedValueOnce(new Error('offline')).mockReturnValueOnce(pending)
  const user = userEvent.setup()
  render(<SupplierVerifyPage />)
  await user.click(await screen.findByRole('button', { name: 'Повторить загрузку заявок' }))
  expect(screen.getByRole('status', { name: 'Загрузка заявок' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Подать заявку на верификацию' })).not.toBeInTheDocument()
  await act(async () => { resolve([]); await pending })
  expect(await screen.findByRole('button', { name: 'Подать заявку на верификацию' })).toBeInTheDocument()
})

it('keeps genuine empty results and local country switching usable without submitting', async () => {
  const user = userEvent.setup()
  render(<SupplierVerifyPage />)
  expect(await screen.findByText(emptyRegistry)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Подать заявку на верификацию' }))
  await user.click(screen.getByRole('button', { name: /Китай/ }))
  expect(screen.getByLabelText('USCC — Unified Social Credit Code *')).toBeInTheDocument()
  expect(api.suppliers.submit).not.toHaveBeenCalled()
})

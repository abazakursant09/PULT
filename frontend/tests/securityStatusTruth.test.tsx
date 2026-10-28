import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SecurityPage from '@/app/dashboard/security/page'
import { api } from '@/lib/api'
import { StrictMode } from 'react'

const { router } = vi.hoisted(() => ({ router: { push: vi.fn() } }))

vi.mock('next/navigation', () => ({ useRouter: () => router }))
vi.mock('@/lib/session', () => ({ isAuthenticated: () => true }))

const loadError = 'Не удалось загрузить статус двухфакторной аутентификации'
const retryLabel = 'Повторить загрузку'

function expectNoOffState() {
  expect(screen.queryByText('Выключено')).not.toBeInTheDocument()
  expect(screen.queryByText('Дополнительная защита аккаунта не активирована')).not.toBeInTheDocument()
}

function expectNoMutationControls() {
  expect(screen.queryByRole('button', { name: 'Включить 2FA', exact: true })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Отключить', exact: true })).not.toBeInTheDocument()
  expect(screen.queryByText('Настройка приложения аутентификатора')).not.toBeInTheDocument()
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
}

beforeEach(() => {
  vi.restoreAllMocks()
  router.push.mockClear()
  vi.spyOn(api.mfa, 'status').mockResolvedValue({ enabled: false })
  vi.spyOn(api.mfa, 'setup').mockImplementation(() => Promise.reject(new Error('Unexpected setup')))
  vi.spyOn(api.mfa, 'verify').mockImplementation(() => Promise.reject(new Error('Unexpected verify')))
  vi.spyOn(api.mfa, 'disable').mockImplementation(() => Promise.reject(new Error('Unexpected disable')))
})

afterEach(() => {
  cleanup()
  expect(api.mfa.setup).not.toHaveBeenCalled()
  expect(api.mfa.verify).not.toHaveBeenCalled()
  expect(api.mfa.disable).not.toHaveBeenCalled()
})

it('announces a rejected status with retry without claiming MFA is off', async () => {
  vi.mocked(api.mfa.status).mockRejectedValue(new Error('offline'))
  render(<SecurityPage />)

  expect(await screen.findByRole('alert')).toHaveTextContent(loadError)
  expect(screen.getByRole('button', { name: retryLabel })).toBeEnabled()
  expectNoOffState()
  expectNoMutationControls()
  expect(api.mfa.status).toHaveBeenCalledTimes(1)
})

it('does not show OFF or setup controls while the initial status is pending', async () => {
  let resolveStatus!: (status: { enabled: boolean }) => void
  vi.mocked(api.mfa.status).mockImplementation(() => new Promise(resolve => { resolveStatus = resolve }))
  render(<SecurityPage />)

  expect(api.mfa.status).toHaveBeenCalledTimes(1)
  expectNoOffState()
  expectNoMutationControls()
  expect(screen.queryByText('✓ Включено')).not.toBeInTheDocument()

  await act(async () => { resolveStatus({ enabled: true }) })
  expect(await screen.findByText('✓ Включено')).toBeInTheDocument()
})

it('retries a failed status and displays only the confirmed enabled state', async () => {
  let resolveRetry!: (status: { enabled: boolean }) => void
  vi.mocked(api.mfa.status)
    .mockRejectedValueOnce(new Error('offline'))
    .mockImplementationOnce(() => new Promise(resolve => { resolveRetry = resolve }))
  render(<SecurityPage />)

  expect(await screen.findByRole('alert')).toHaveTextContent(loadError)
  fireEvent.click(screen.getByRole('button', { name: retryLabel }))
  expect(api.mfa.status).toHaveBeenCalledTimes(2)
  expectNoOffState()
  expectNoMutationControls()

  await act(async () => { resolveRetry({ enabled: true }) })
  expect(await screen.findByText('✓ Включено')).toBeInTheDocument()
  expect(screen.getByText('Аккаунт защищён TOTP-кодом при каждом входе')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Отключить', exact: true })).toBeEnabled()
  expectNoOffState()
  expect(screen.queryByRole('button', { name: 'Включить 2FA' })).not.toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: retryLabel })).not.toBeInTheDocument()
})

it('offers enable only after a confirmed false status', async () => {
  render(<SecurityPage />)

  expect(await screen.findByRole('button', { name: 'Включить 2FA' })).toBeEnabled()
  expect(screen.getByText('Выключено')).toBeInTheDocument()
  expect(screen.getByText('Дополнительная защита аккаунта не активирована')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Отключить', exact: true })).not.toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('offers disable only after a confirmed true status', async () => {
  vi.mocked(api.mfa.status).mockResolvedValue({ enabled: true })
  render(<SecurityPage />)

  expect(await screen.findByRole('button', { name: 'Отключить', exact: true })).toBeEnabled()
  expect(screen.getByText('✓ Включено')).toBeInTheDocument()
  expectNoOffState()
  expect(screen.queryByRole('button', { name: 'Включить 2FA' })).not.toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('treats a malformed status as unknown rather than disabled', async () => {
  vi.mocked(api.mfa.status).mockResolvedValue({} as Awaited<ReturnType<typeof api.mfa.status>>)
  render(<SecurityPage />)

  expect(await screen.findByRole('alert')).toHaveTextContent(loadError)
  expect(screen.getByRole('button', { name: retryLabel })).toBeEnabled()
  expectNoOffState()
  expectNoMutationControls()
})

it('ignores a stale status result after StrictMode restarts the effect', async () => {
  const resolve: Array<(status: { enabled: boolean }) => void> = []
  vi.mocked(api.mfa.status).mockImplementation(() => new Promise(done => { resolve.push(done) }))
  render(<StrictMode><SecurityPage /></StrictMode>)
  expect(resolve).toHaveLength(2)
  await act(async () => resolve[1]({ enabled: true }))
  expect(await screen.findByText('✓ Включено')).toBeInTheDocument()
  await act(async () => resolve[0]({ enabled: false }))
  expect(screen.getByText('✓ Включено')).toBeInTheDocument()
  expectNoOffState()
})

it('settles a rejected pending status safely after unmount', async () => {
  let reject!: (reason: Error) => void
  vi.mocked(api.mfa.status).mockImplementation(() => new Promise((_done, fail) => { reject = fail }))
  const view = render(<SecurityPage />)
  view.unmount()
  await act(async () => reject(new Error('synthetic late failure')))
  expect(view.container).toBeEmptyDOMElement()
})

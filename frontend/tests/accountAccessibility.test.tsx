import { render, screen, fireEvent } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import AccountPage from '@/app/dashboard/account/page'
import { api } from '@/lib/api'

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => new URLSearchParams('tab=security') }))
vi.mock('@/lib/session', () => ({ isAuthenticated: () => true, clearSession: vi.fn() }))
vi.mock('@/components/LanguageSwitcher', () => ({ LanguageSwitcher: () => null }))

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api.mfa, 'status').mockResolvedValue({ enabled: false })
})

it('does not present unknown MFA status as disabled after loading fails', async () => {
  vi.mocked(api.mfa.status).mockRejectedValue(new Error('offline'))
  const setup = vi.spyOn(api.mfa, 'setup')
  render(<AccountPage />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось проверить состояние 2FA')
  expect(screen.queryByText('Защита не активирована')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Включить 2FA' })).not.toBeInTheDocument()
  expect(setup).not.toHaveBeenCalled()
})

it('names setup controls and announces verification failure and success', async () => {
  vi.spyOn(api.mfa, 'setup').mockResolvedValue({ secret: 'TESTONLY', otpauth: 'otpauth://totp/Test?secret=TESTONLY' })
  const verify = vi.spyOn(api.mfa, 'verify').mockRejectedValueOnce(new Error('Неверный код')).mockResolvedValueOnce({} as never)
  render(<AccountPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Включить 2FA' }))
  const code = await screen.findByRole('textbox', { name: 'Код подтверждения 2FA' })
  expect(screen.getByRole('button', { name: 'Скопировать ключ 2FA' })).toBeInTheDocument()
  fireEvent.change(code, { target: { value: '123456' } })
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Неверный код')
  expect(code).toHaveValue('123456')
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить' }))
  expect(await screen.findByRole('status')).toHaveTextContent('MFA включена')
  expect(verify).toHaveBeenLastCalledWith('123456')
})

it('names the disable code without disabling MFA merely by opening the form', async () => {
  vi.mocked(api.mfa.status).mockResolvedValue({ enabled: true })
  const disable = vi.spyOn(api.mfa, 'disable')
  render(<AccountPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Отключить' }))
  expect(screen.getByRole('textbox', { name: 'Код для отключения 2FA' })).toBeInTheDocument()
  expect(disable).not.toHaveBeenCalled()
})

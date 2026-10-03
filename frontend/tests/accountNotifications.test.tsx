import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import AccountPage from '@/app/dashboard/account/page'
import { api, type TelegramSettings } from '@/lib/api'

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => new URLSearchParams('tab=notifications') }))
vi.mock('@/lib/session', () => ({ isAuthenticated: () => true, clearSession: vi.fn() }))
vi.mock('@/components/LanguageSwitcher', () => ({ LanguageSwitcher: () => null }))
const settings = { daily_report: true, daily_report_time: '09:00', weekly_summary: true,
  weekly_summary_day: 'monday', weekly_summary_time: '10:00', notify_seo_opportunity: false,
  notify_sales_growth: false, notify_insights: false } as TelegramSettings

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api.telegram, 'getChatId').mockResolvedValue({ telegram_chat_id: null })
  vi.spyOn(api.telegram, 'getSettings').mockResolvedValue(settings)
  vi.spyOn(api.telegram, 'test').mockResolvedValue({} as never)
  vi.spyOn(api.telegram, 'triggerInsights').mockResolvedValue({ notifications_sent: 0 } as never)
})

it('announces load failure without offering to overwrite unknown settings', async () => {
  vi.mocked(api.telegram.getSettings).mockRejectedValue(new Error('offline'))
  render(<AccountPage />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить настройки Telegram')
  expect(screen.queryByRole('button', { name: 'Сохранить настройки' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument()
})

it('also fails closed when loading the existing chat ID fails', async () => {
  vi.mocked(api.telegram.getChatId).mockRejectedValue(new Error('offline'))
  render(<AccountPage />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить настройки Telegram')
  expect(screen.queryByRole('textbox', { name: 'Telegram Chat ID' })).not.toBeInTheDocument()
})

it('announces a failed chat ID save without losing the edited value', async () => {
  vi.spyOn(api.telegram, 'updateChatId').mockRejectedValue(new Error('Не удалось сохранить Chat ID'))
  render(<AccountPage />)
  const input = await screen.findByRole('textbox', { name: 'Telegram Chat ID' })
  fireEvent.change(input, { target: { value: '123456' } })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить', exact: true }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось сохранить Chat ID')
  expect(input).toHaveValue('123456')
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it('preserves an edit on failure, clears the error on retry and announces success', async () => {
  const save = vi.spyOn(api.telegram, 'updateSettings').mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce({ ...settings, daily_report: false })
  render(<AccountPage />)
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Ежедневный отчёт' }))
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить настройки' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось сохранить настройки')
  expect(screen.getByRole('checkbox', { name: 'Ежедневный отчёт' })).not.toBeChecked()
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить настройки' }))
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  expect(await screen.findByRole('status')).toHaveTextContent('Настройки сохранены')
  expect(save).toHaveBeenLastCalledWith({ ...settings, daily_report: false })
  expect(api.telegram.test).not.toHaveBeenCalled()
  expect(api.telegram.triggerInsights).not.toHaveBeenCalled()
})

it('names chat, switches and schedule fields and exposes the selected weekday', async () => {
  render(<AccountPage />)
  expect(await screen.findByRole('textbox', { name: 'Telegram Chat ID' })).toBeInTheDocument()
  for (const name of ['SEO-возможности', 'Рост продаж и рейтинга', 'Критические алерты', 'Ежедневный отчёт', 'Еженедельная сводка']) {
    expect(screen.getByRole('checkbox', { name })).toBeInTheDocument()
  }
  expect(screen.getByLabelText('Время ежедневного отчёта')).toHaveValue('09:00')
  expect(screen.getByLabelText('Время еженедельной сводки')).toHaveValue('10:00')
  expect(screen.getByRole('button', { name: 'Пн', pressed: true })).toBeInTheDocument()
})

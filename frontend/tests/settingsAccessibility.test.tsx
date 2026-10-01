import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsPage from '@/app/dashboard/settings/page'
import { api, type TelegramSettings } from '@/lib/api'

vi.mock('@/components/seller/Shell', () => ({ SellerBar: () => null }))
vi.mock('@/components/connections/ConnectionsSection', () => ({ ConnectionsSection: () => null }))
vi.mock('@/components/theme/ThemePicker', () => ({ ThemePicker: () => null }))

const settings = {
  daily_report: true, daily_report_time: '09:00', weekly_summary: true,
  weekly_summary_day: 'monday', weekly_summary_time: '10:00',
} as TelegramSettings

describe('Settings error feedback and accessible controls', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api.telegram, 'getChatId').mockResolvedValue({ telegram_chat_id: null })
    vi.spyOn(api.telegram, 'getSettings').mockResolvedValue(settings)
  })

  it('announces failed loading rather than silently hiding settings', async () => {
    vi.mocked(api.telegram.getSettings).mockRejectedValue(new Error('unavailable'))
    render(<SettingsPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить настройки Telegram. Попробуйте обновить страницу.')
    expect(screen.queryByRole('button', { name: 'Сохранить настройки' })).not.toBeInTheDocument()
  })

  it('preserves edited settings after save failure and allows a successful retry', async () => {
    const save = vi.spyOn(api.telegram, 'updateSettings')
      .mockRejectedValueOnce(new Error('unavailable'))
      .mockResolvedValueOnce({ ...settings, daily_report: false })
    render(<SettingsPage />)
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Ежедневный отчёт' }))
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить настройки' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось сохранить настройки. Попробуйте ещё раз.')
    expect(screen.getByRole('checkbox', { name: 'Ежедневный отчёт' })).not.toBeChecked()
    expect(screen.queryByText('Сохранено')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить настройки' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(await screen.findByText('Сохранено')).toBeInTheDocument()
    expect(save).toHaveBeenCalledTimes(2)
    expect(save).toHaveBeenLastCalledWith({ ...settings, daily_report: false })
  })

  it('names the chat ID, both switches and both time inputs', async () => {
    render(<SettingsPage />)
    expect(await screen.findByRole('checkbox', { name: 'Ежедневный отчёт' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Еженедельная сводка' })).toBeChecked()
    expect(screen.getByRole('textbox', { name: 'Telegram Chat ID' })).toBeInTheDocument()
    expect(screen.getByLabelText('Время ежедневного отчёта')).toHaveValue('09:00')
    expect(screen.getByLabelText('Время еженедельной сводки')).toHaveValue('10:00')
  })
})

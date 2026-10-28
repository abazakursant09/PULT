import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LandingPage from '@/app/page'

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/components/NotificationBell', () => ({ NotificationBell: () => null }))

describe('Approved light landing restoration', () => {
  beforeEach(() => localStorage.clear())

  it('restores the approved hero and keeps demonstration figures explicitly marked', () => {
    render(<LandingPage />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Не смотреть на бизнес\.\s*Управлять им\./)
    expect(screen.getByText('ДЕМО-СЦЕНАРИЙ')).toBeVisible()
    expect(screen.getByText(/Продукт готовится к коммерческому запуску/)).toBeVisible()
    expect(screen.getByRole('link', { name: /Посмотреть, как работает/ })).toHaveAttribute('href', '#product')
  })

  it('uses the unchanged approved reference on every mark, including the footer', () => {
    const { container } = render(<LandingPage />)
    const marks = container.querySelectorAll('svg image')
    expect(marks).toHaveLength(4)
    marks.forEach(mark => expect(mark).toHaveAttribute('href', '/logos/pult-approved-42.webp'))
  })

  it('does not advertise unapproved commercial terms or blanket security assurances', () => {
    const { container } = render(<LandingPage />)
    expect(container.textContent).not.toMatch(/14 дней|1[ \u00a0]?990|4[ \u00a0]?990|Без карты|152-ФЗ|API-ключи не нужны|Только публичные данные|не передаются третьим лицам/i)
    expect(screen.getAllByRole('link', { name: /Создать аккаунт/ }).every(link => link.getAttribute('href') === '/register')).toBe(true)
  })
})

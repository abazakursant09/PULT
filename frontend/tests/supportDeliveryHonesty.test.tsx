import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import SupportPage from '@/app/support/page'

vi.mock('@/components/ui/blur-fade', () => ({
  BlurFade: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

describe('support delivery honesty while no delivery endpoint exists', () => {
  it('states the limitation before asking for personal message data', () => {
    const { container } = render(<SupportPage />)
    expect(screen.getByRole('note', { name: 'Отправка через сайт недоступна' })).toBeInTheDocument()
    expect(screen.getByText('Сообщения на этой странице не собираются и не отправляются.')).toBeInTheDocument()
    expect(container.querySelector('form')).toBeNull()
    expect(container.querySelector('input, textarea, select')).toBeNull()
    expect(screen.queryByRole('button', { name: /^Отправить/ })).toBeNull()
  })

  it('never claims successful delivery or an incoming reply from this page', () => {
    render(<SupportPage />)
    expect(screen.queryByText('Сообщение отправлено!')).toBeNull()
    expect(screen.queryByText(/мы свяжемся с вами в ближайшее время по адресу/)).toBeNull()
  })

  it('does not alter or read historical browser-only tickets', () => {
    const existing = '[{"message":"synthetic fixture, not a real ticket"}]'
    localStorage.setItem('bp_support_tickets', existing)
    const get = vi.spyOn(Storage.prototype, 'getItem')
    const set = vi.spyOn(Storage.prototype, 'setItem')
    const remove = vi.spyOn(Storage.prototype, 'removeItem')
    const clear = vi.spyOn(Storage.prototype, 'clear')
    const view = render(<SupportPage />)
    view.unmount()
    expect(get).not.toHaveBeenCalledWith('bp_support_tickets')
    expect(set.mock.calls.filter(([key]) => key === 'bp_support_tickets')).toEqual([])
    expect(remove).not.toHaveBeenCalledWith('bp_support_tickets')
    expect(clear).not.toHaveBeenCalled()
    get.mockRestore(); set.mockRestore(); remove.mockRestore(); clear.mockRestore()
    expect(localStorage.getItem('bp_support_tickets')).toBe(existing)
  })

  it('preserves existing contact links without activating a new address', () => {
    render(<SupportPage />)
    const links = screen.getAllByRole('link').filter(link => link.getAttribute('href')?.startsWith('mailto:'))
    expect(links).toHaveLength(2)
    links.forEach(link => expect(link).toHaveAttribute('href', 'mailto:hello@biznes-pult.ru'))
    expect(screen.getByRole('link', { name: 'hello@biznes-pult.ru' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Открыть почтовое приложение' })).toHaveAttribute('href', 'mailto:hello@biznes-pult.ru')
  })

  it('contains no fake local-ticket or delivery side effect', () => {
    const source = readFileSync(join(__dirname, '../app/support/page.tsx'), 'utf8')
    expect(source).not.toMatch(/bp_support_tickets|localStorage|sessionStorage|setSent|fetch\s*\(|axios|api\./)
  })

  it('documents the old ticket key as historical, not current storage', () => {
    const notice = readFileSync(join(__dirname, '../../docs/legal/cookie-notice.DRAFT.md'), 'utf8')
    const current = notice.match(/## Локальное хранилище[\s\S]*?(?=\n## )/)?.[0]
    expect(current).toBeTruthy()
    expect(current).not.toContain('bp_support_tickets')
    const historical = notice.match(/## Историческое локальное хранилище поддержки[\s\S]*?(?=\n## )/)?.[0]
    expect(historical).toContain('bp_support_tickets')
    expect(historical).toContain('больше не читает и не записывает')
    expect(historical).toContain('существующие записи автоматически не удаляются')
    expect(notice).toContain('DRAFT — НЕ ПУБЛИКОВАТЬ')
    expect(notice).toContain('REQUIRES RUSSIAN COUNSEL REVIEW')
  })

  it('uses the approved reference mark without rebuilding its geometry', () => {
    render(<SupportPage />)
    const brand = screen.getByRole('link', { name: 'Пульт — на главную' })
    expect(brand.querySelector('svg')).toHaveAttribute('viewBox', '0 0 340 382')
    expect(brand.querySelector('image')).toHaveAttribute('href', '/logos/pult-approved-42.webp')
    expect(brand.querySelector('path')).toBeNull()
  })

  it('shows practical cabinet questions and native expandable answers', () => {
    const { container } = render(<SupportPage />)
    expect(screen.getByRole('heading', { level: 1, name: 'Помощь по работе с Пультом' })).toBeInTheDocument()
    expect(container.querySelectorAll('details')).toHaveLength(4)
    expect(container.querySelector('details')).toHaveAttribute('open')
    expect(container.querySelectorAll('summary')).toHaveLength(4)
    expect(screen.getByText('Как добавить кабинет?')).toBeInTheDocument()
    expect(screen.getByText('Где загрузить отчёт?')).toBeInTheDocument()
    expect(screen.getByText('Как изменить тему кабинета?')).toBeInTheDocument()
    expect(screen.getByText('Как восстановить доступ?')).toBeInTheDocument()
  })

  it('does not invent monitoring, commercial guarantees or working hours', () => {
    const { container } = render(<SupportPage />)
    expect(screen.getByText('На этой странице нет подключения к мониторингу доступности.')).toBeInTheDocument()
    expect(screen.getByText('Статус не проверяется')).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/14 дней|без привязки карты|тарифа «Мастер»|Ответим в течение|Ответ в течение|Время работы|замедление|Пн — Пт|не передаём ваши данные третьим лицам/)
    expect(readFileSync(join(__dirname, '../app/support/page.tsx'), 'utf8')).not.toContain('STATUS_ITEMS')
  })

  it('has only declared destinations, real anchors and visible account navigation', () => {
    const { container } = render(<SupportPage />)
    const allowed = new Set(['/', '/login', '/dashboard', '/dashboard/stores', '/forgot-password', 'mailto:hello@biznes-pult.ru', '#top', '#contact'])
    for (const link of screen.getAllByRole('link')) {
      const href = link.getAttribute('href')!
      expect(allowed.has(href), href).toBe(true)
      if (href.startsWith('#')) expect(container.querySelector(href)).not.toBeNull()
    }
    expect(screen.getByRole('link', { name: 'Войти' })).toHaveAttribute('href', '/login')
    expect(screen.getByRole('link', { name: 'Восстановить доступ →' })).toHaveAttribute('href', '/forgot-password')
  })

  it('owns the approved light palette without global or seller-theme overrides', () => {
    const css = readFileSync(join(__dirname, '../app/support/support.module.css'), 'utf8')
    expect(css).toContain('.page {')
    for (const token of ['#151827', '#565c6e', '#5d6575', '#4337a8', '#e7e9f0', '#f7f8fb']) expect(css).toContain(token)
    expect(css).not.toMatch(/:root|:global|data-theme|^\s*(?:html|body)\s*[{,]/m)
    expect(css).not.toMatch(/(?:^|[{},])\s*(?:main|footer|button)\s*[{,]/m)
    expect(css).toMatch(/prefers-reduced-motion\s*:\s*reduce/)
  })
})

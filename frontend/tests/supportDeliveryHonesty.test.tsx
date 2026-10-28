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
    expect(screen.getByText(/Сообщения через эту страницу не отправляются/)).toBeInTheDocument()
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
    const links = screen.getAllByRole('link', { name: 'hello@biznes-pult.ru' })
    expect(links).toHaveLength(2)
    links.forEach(link => expect(link).toHaveAttribute('href', 'mailto:hello@biznes-pult.ru'))
  })

  it('contains no fake local-ticket or delivery side effect', () => {
    const source = readFileSync(join(__dirname, '../app/support/page.tsx'), 'utf8')
    expect(source).not.toMatch(/bp_support_tickets|localStorage|sessionStorage|setSent|fetch\s*\(|axios|api\./)
  })
})

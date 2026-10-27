import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import StartupPage from '@/app/startup/page'
import { routerPush } from './setup'

const { flags } = vi.hoisted(() => ({ flags: { billing: true, growthContour: false } }))
vi.mock('@/lib/featureFlags', () => ({ FLAGS: flags }))

describe('onboarding links respect independent release flags', () => {
  it.each([false, true])('shows the academy action only when growth=%s', growth => {
    flags.growthContour = growth
    vi.useFakeTimers()
    try {
      render(<StartupPage />)
      const selects = screen.getAllByRole('combobox')
      fireEvent.change(selects[0], { target: { value: 'Одежда' } })
      fireEvent.change(selects[1], { target: { value: 'Средний' } })
      fireEvent.click(screen.getByRole('button', { name: 'Нет' }))
      fireEvent.click(screen.getByRole('button', { name: /Получить рекомендацию/ }))
      expect(screen.getByRole('button', { name: /Оформить «Старт»/ })).toBeInTheDocument()
      const academy = screen.queryByRole('button', { name: 'Вернуться к бесплатной версии' })
      if (growth) {
        expect(academy).toBeInTheDocument()
        fireEvent.click(academy!)
        expect(routerPush).toHaveBeenCalledWith('/academy')
      } else {
        expect(academy).not.toBeInTheDocument()
        expect(routerPush).not.toHaveBeenCalled()
      }
    } finally { vi.clearAllTimers(); vi.useRealTimers() }
  })
})

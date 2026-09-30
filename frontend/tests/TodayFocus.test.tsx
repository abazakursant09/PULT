import { render, screen, fireEvent } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TodayFocus from '@/components/decision-feed/TodayFocus'
import { api, type TodayItem } from '@/lib/api'
import { diagnosisCard } from './fixtures'

const top: TodayItem = {
  item_key: 'focus-test', contour: 'advertising', marketplace: 'wildberries', sku: 'ART-1001',
  title: 'Проверить рекламу', what_happened: 'Расходы выросли', why_it_matters: 'Маржа снижается',
  recommended_action: 'Проверить бюджет', expected_effect: 'Оценить перерасход', meaning: null,
  source_status: 'open', attention_state: 'new', effect_status: null, effect_band: null,
  group_key: null, action_key: null, action_role: null, learning_context: null,
  created_at: null, updated_at: null,
}

describe('TodayFocus compact presentation', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('reports absent recommendations without claiming analysis is running', async () => {
    vi.spyOn(api.today, 'get').mockResolvedValue({ items: [], total: 0, top_action: null })
    vi.spyOn(api.presentation, 'getCards').mockResolvedValue({ cards: [] })
    const { container } = render(<TodayFocus />)
    expect(await screen.findByRole('status')).toHaveTextContent('Главная рекомендация пока отсутствует')
    expect(screen.queryByText(/анализирует|автоматически/)).not.toBeInTheDocument()
    expect(container.querySelector('details')).toBeNull()
  })

  it('announces a load error with a recovery instruction, not an empty success', async () => {
    vi.spyOn(api.today, 'get').mockRejectedValue(new Error('Нет соединения'))
    vi.spyOn(api.presentation, 'getCards').mockResolvedValue({ cards: [] })
    render(<TodayFocus />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Попробуйте обновить страницу')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('keeps the action visible and preserves evidence in a native disclosure without mutation', async () => {
    const get = vi.spyOn(api.today, 'get').mockResolvedValue({ items: [top], total: 1, top_action: top })
    vi.spyOn(api.presentation, 'getCards').mockResolvedValue({ cards: [diagnosisCard] })
    const mark = vi.spyOn(api.decisionFeed, 'markSeen')
    const { container } = render(<TodayFocus />)
    expect(await screen.findByText('Проверить бюджет')).toBeVisible()
    const details = container.querySelector('details')!
    expect(details).not.toHaveAttribute('open')
    expect(details).toHaveTextContent('Маржа снижается')
    expect(details).toHaveTextContent('Оценить перерасход')
    expect(details).toHaveTextContent(diagnosisCard.root_cause_narrative!)
    expect(details.querySelector('summary')).toHaveTextContent('Почему это важно')
    fireEvent.click(details.querySelector('summary')!)
    expect(mark).not.toHaveBeenCalled()
    expect(get).toHaveBeenCalledTimes(1)
  })

  it('does not invent evidence when the response has none', async () => {
    vi.spyOn(api.today, 'get').mockResolvedValue({ items: [], total: 1, top_action: { ...top, why_it_matters: null, expected_effect: null } })
    vi.spyOn(api.presentation, 'getCards').mockResolvedValue({ cards: [] })
    const { container } = render(<TodayFocus />)
    await screen.findByText('Проверить бюджет')
    expect(container.querySelector('details')).toBeNull()
  })
})

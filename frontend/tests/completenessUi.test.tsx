import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { CompletenessNote, ConflictBanner, SourceTag } from '@/components/stores/Completeness'

// PULT-LAUNCH-1.4.5I §7/§8/§9 — honest completeness + conflict. Missing data reads as "нет данных",
// never 0; an exact profit is never shown while a required input is missing; a source conflict shows
// both numbers and resolves via a real policy choice, never a sum.

describe('completeness + conflict surfaces', () => {
  it('names the source, never a raw value', () => {
    const { rerender } = render(<SourceTag source="api" />)
    expect(screen.getByText('Источник: API')).toBeInTheDocument()
    rerender(<SourceTag source="csv" />)
    expect(screen.getByText('Источник: CSV')).toBeInTheDocument()
  })

  it('says cost of goods is missing and profit is not computed', () => {
    render(<CompletenessNote completeness="incomplete" missingFields={['cogs']} />)
    expect(screen.getByText(/Нет данных о себестоимости\. Прибыль и маржа не рассчитаны\./)).toBeInTheDocument()
  })

  it('says ad spend is missing and ДРР is not computed', () => {
    render(<CompletenessNote completeness="incomplete" missingFields={['ad_spend']} />)
    expect(screen.getByText(/Нет данных о рекламных расходах\. ДРР не рассчитан\./)).toBeInTheDocument()
  })

  it('explains incomplete product attribution without hiding it', () => {
    render(<CompletenessNote completeness="incomplete" missingFields={['product_attribution']} />)
    expect(screen.getByText(/Итоги магазина полные, а показатели отдельных товаров — неполные/)).toBeInTheDocument()
  })

  it('shows "нет данных", not 0, when there is no data', () => {
    render(<CompletenessNote completeness="no_data" />)
    expect(screen.getByText('Нет данных.')).toBeInTheDocument()
    expect(screen.queryByText('0')).toBeNull()
  })

  it('a conflict shows both values, the safe choice, and resolves via a real decision — never a sum', async () => {
    const onChoose = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(
      <div className="ledger">
        <ConflictBanner metricLabel="Выручка" period="2026-07" apiValue="250 ₽" csvValue="100 ₽"
                        chosen="csv" onChoose={onChoose} />
      </div>,
    )
    expect(screen.getByText(/Есть расхождение API и CSV/)).toBeInTheDocument()
    expect(screen.getByText('250 ₽')).toBeInTheDocument()
    expect(screen.getByText('100 ₽')).toBeInTheDocument()
    expect(screen.getByText(/Сейчас: CSV/)).toBeInTheDocument()
    // no summed value shown
    expect(screen.queryByText('350 ₽')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Использовать API' }))
    expect(onChoose).toHaveBeenCalledWith('api')
  })
})

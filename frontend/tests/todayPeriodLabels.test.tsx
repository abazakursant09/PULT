import { render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import BusinessToday from '@/components/dashboard/BusinessToday'
import { api } from '@/lib/api'
import { todayWithData } from './fixtures'

beforeEach(() => vi.restoreAllMocks())

it('names the real day and comparison rather than claiming today', async () => {
  vi.spyOn(api.today, 'getSummary').mockResolvedValue(todayWithData)
  render(<BusinessToday />)
  expect(await screen.findByText('29.10.2026')).toBeInTheDocument()
  expect(screen.getAllByText('За 29.10.2026')).toHaveLength(2)
  expect(screen.getByText('Изменение к 28.10.2026')).toBeInTheDocument()
  expect(screen.getByText('-12%')).toBeInTheDocument()
  expect(screen.queryByText('Прибыль сегодня')).not.toBeInTheDocument()
})

it('shows exact range and marks an unfinished period preliminary', async () => {
  vi.spyOn(api.today, 'getSummary').mockResolvedValue({...todayWithData,
    period_from: '2026-09-30', period_to: '2026-10-30', is_partial: true,
    comparison_from: null, comparison_to: null, delta_revenue_pct: null})
  render(<BusinessToday />)
  expect(await screen.findByText('30.09.2026 — 30.10.2026')).toBeInTheDocument()
  expect(screen.getByText('Предварительные')).toBeInTheDocument()
  expect(screen.queryByText(/последние 30 дней/i)).not.toBeInTheDocument()
})

it('does not invent a comparison when an old response contains a percentage without dates', async () => {
  vi.spyOn(api.today, 'getSummary').mockResolvedValue({...todayWithData,
    comparison_from: undefined, comparison_to: undefined})
  render(<BusinessToday />)
  expect(await screen.findByText('Сравнение недоступно')).toBeInTheDocument()
  expect(screen.queryByText('-12%')).not.toBeInTheDocument()
})

it.each([undefined, '2026-02-30', 'not-a-date'])('fails closed for missing or invalid period %s', async value => {
  vi.spyOn(api.today, 'getSummary').mockResolvedValue({...todayWithData,
    period_from: value, period_to: value})
  render(<BusinessToday />)
  expect(await screen.findAllByText('Период не указан')).toHaveLength(3)
  expect(screen.queryByText('За сегодня')).not.toBeInTheDocument()
})

it('keeps leap-day and year-boundary dates without local timezone conversion', async () => {
  vi.spyOn(api.today, 'getSummary').mockResolvedValue({...todayWithData,
    period_from: '2028-02-29', period_to: '2028-02-29',
    comparison_from: '2027-12-31', comparison_to: '2027-12-31'})
  render(<BusinessToday />)
  expect(await screen.findByText('29.02.2028')).toBeInTheDocument()
  expect(screen.getByText('Изменение к 31.12.2027')).toBeInTheDocument()
})

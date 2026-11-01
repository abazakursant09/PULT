import { render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import SuppliersPage from '@/app/suppliers/page'
import { api, type SupplierEntry } from '@/lib/api'

vi.mock('@/components/AppShell', () => ({ AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</> }))
vi.mock('@/components/ui/blur-fade', () => ({ BlurFade: ({ children }: { children: React.ReactNode }) => <>{children}</> }))
afterEach(() => vi.restoreAllMocks())

it.each([true, false])('does not endorse a catalogue row with legacy is_verified=%s', async is_verified => {
  const row: SupplierEntry = {
    id: 'synthetic-supplier', company_name: 'Synthetic retained company', industry: null,
    region: null, country: 'russia', description: 'Synthetic preserved description', website: null,
    phone: null, min_order_qty: null, is_verified, rating: 0, total_reviews: 0, total_deals: 0,
  }
  vi.spyOn(api.catalog, 'listSuppliers').mockResolvedValue([row])
  const fetch = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('Unexpected transport'))
  const reviews = vi.spyOn(api.supplierReviews, 'list').mockResolvedValue([])
  const create = vi.spyOn(api.deals, 'create').mockRejectedValue(new Error('Mutation forbidden'))
  render(<SuppliersPage />)
  expect(await screen.findByText(row.company_name)).toBeInTheDocument()
  expect(screen.getByText(row.description!)).toBeInTheDocument()
  expect(screen.queryByText(/Верифицирован|Проверенный производитель/)).not.toBeInTheDocument()
  expect(api.catalog.listSuppliers).toHaveBeenCalledWith(expect.objectContaining({ sort: 'rating' }))
  expect(fetch).not.toHaveBeenCalled()
  expect(reviews).not.toHaveBeenCalled()
  expect(create).not.toHaveBeenCalled()
})

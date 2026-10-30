import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'

const response = (status = 200) => new Response(JSON.stringify({ detail: 'synthetic response' }), { status })
const mutations = [
  { method: 'POST', invoke: () => api.csvImport.confirm('synthetic-wire-import', 'new') },
  { method: 'PATCH', invoke: () => api.reviews.update('synthetic-product', 'synthetic-review', { response_text: 'Synthetic' }) },
  { method: 'PUT', invoke: () => api.telegram.updateSettings({ daily_report: false }) },
  { method: 'DELETE', invoke: () => api.products.delete('synthetic-product') },
]

beforeEach(() => vi.useFakeTimers())
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

async function settle(promise: Promise<unknown>) {
  const observed = promise.catch(error => error)
  await vi.advanceTimersByTimeAsync(2000)
  return observed
}

it.each(mutations)('$method is attempted once after an ambiguous HTTP500 response', async ({ method, invoke }) => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => response(500))
  expect(await settle(invoke())).toMatchObject({ status: 500 })
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(fetch.mock.calls[0][1]).toMatchObject({ method, credentials: 'include' })
})

it.each(mutations)('$method is attempted once after a network failure', async ({ invoke }) => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('synthetic offline'))
  expect(await settle(invoke())).toBeInstanceOf(TypeError)
  expect(fetch).toHaveBeenCalledTimes(1)
})

it.each(['http', 'network'])('multipart upload is attempted once after %s failure', async failure => {
  const fetch = vi.spyOn(globalThis, 'fetch')
  if (failure === 'http') fetch.mockImplementation(async () => response(500))
  else fetch.mockRejectedValue(new TypeError('synthetic offline'))
  const outcome = await settle(api.csvImport.upload(new File(['sku\nSYN-1'], 'synthetic.csv'), 'synthetic-store'))
  expect(outcome).toBeInstanceOf(Error)
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'POST', credentials: 'include', body: expect.any(FormData) })
})

it('preserves explicit idempotency metadata without inferring permission for automatic repeats', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => response(500))
  await settle(api.reviews.publish('synthetic-product', 'synthetic-review', 'synthetic-operation'))
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(fetch.mock.calls[0][1]?.headers).toMatchObject({ 'Idempotency-Key': 'synthetic-operation' })
})

it.each(['http', 'network'])('GET keeps bounded retries after %s failures', async failure => {
  let count = 0
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
    count++
    if (count < 3) {
      if (failure === 'network') throw new TypeError('synthetic offline')
      return response(500)
    }
    return new Response('[]', { status: 200 })
  })
  expect(await settle(api.monitor.list())).toEqual([])
  expect(fetch).toHaveBeenCalledTimes(3)
})

it('GET still stops after three failed attempts', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => response(500))
  expect(await settle(api.monitor.list())).toMatchObject({ status: 500 })
  expect(fetch).toHaveBeenCalledTimes(3)
})

it('abort remains a single attempt', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new DOMException('Synthetic abort', 'AbortError'))
  expect(await settle(api.csvImport.confirm('synthetic-wire-import', 'new'))).toMatchObject({ name: 'AbortError' })
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('a later explicit invocation is still possible without hidden automatic attempts', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementationOnce(async () => response(500))
    .mockImplementation(async () => response())
  expect(await settle(api.csvImport.confirm('synthetic-wire-import', 'new'))).toMatchObject({ status: 500 })
  expect(fetch).toHaveBeenCalledTimes(1)
  await api.csvImport.confirm('synthetic-wire-import', 'new')
  expect(fetch).toHaveBeenCalledTimes(2)
})

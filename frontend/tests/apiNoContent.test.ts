import { afterEach, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'

afterEach(() => vi.restoreAllMocks())

it('accepts the real logout 204 response without parsing a nonexistent JSON body', async () => {
  const response = new Response(null, { status: 204 })
  const parse = vi.spyOn(response, 'json')
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response)
  await expect(api.auth.logout()).resolves.toBeUndefined()
  expect(parse).not.toHaveBeenCalled()
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'POST', credentials: 'include' })
})

it('still parses ordinary successful JSON responses', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ id: 'synthetic-user' })))
  await expect(api.auth.me()).resolves.toEqual({ id: 'synthetic-user' })
})

it('does not silently accept an invalid empty HTTP200 response', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 200 }))
  await expect(api.auth.logout()).rejects.toBeInstanceOf(SyntaxError)
})

it('does not repeat logout after a network error', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('synthetic offline'))
  await expect(api.auth.logout()).rejects.toThrow('synthetic offline')
  expect(fetch).toHaveBeenCalledTimes(1)
})

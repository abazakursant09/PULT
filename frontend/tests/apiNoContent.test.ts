import { afterEach, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'

afterEach(() => vi.restoreAllMocks())

it('sends explicit versioned consent to the new closure endpoint without legacy DELETE', async () => {
  const result = { ok: true, state: 'closed_recoverable', recovery_deadline: '2026-12-10T12:00:00Z', erasure_complete: false }
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(result)))
  await expect(api.account.close()).resolves.toEqual(result)
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(String(fetch.mock.calls[0][0])).toContain('/api/account/close')
  expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'POST', credentials: 'include' })
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({ policy_version: 'normal-close-30d-v1', confirm: true })
})

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

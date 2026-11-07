import { EventEmitter } from 'events'
import type { ChildProcess } from 'child_process'
import { afterEach, expect, test, vi } from 'vitest'
import { stopOwnedProcess } from '../e2e/pg-process'

function fakeProcess(exitOn?: string) {
  const process = Object.assign(new EventEmitter(), {
    exitCode: null as number | null,
    signalCode: null as string | null,
    kill: vi.fn((signal: string) => {
      if (signal === exitOn) {
        process.signalCode = signal
        process.emit('exit', null, signal)
      }
      return true
    }),
  })
  return process
}
afterEach(() => { vi.useRealTimers() })

test('graceful shutdown does not escalate', async () => {
  const child = fakeProcess('SIGTERM')
  await stopOwnedProcess(child as unknown as ChildProcess)
  expect(child.kill.mock.calls).toEqual([['SIGTERM']])
})

test('hung graceful shutdown force-stops only the owned handle', async () => {
  vi.useFakeTimers()
  const child = fakeProcess('SIGKILL')
  const stopped = stopOwnedProcess(child as unknown as ChildProcess, 10)
  await vi.advanceTimersByTimeAsync(10)
  await stopped
  expect(child.kill.mock.calls).toEqual([['SIGTERM'], ['SIGKILL']])
  expect(vi.getTimerCount()).toBe(0)
})

test('already exited child requires no signal', async () => {
  const child = fakeProcess()
  child.exitCode = 0
  await stopOwnedProcess(child as unknown as ChildProcess)
  expect(child.kill).not.toHaveBeenCalled()
})

test('even failed force-stop has a bounded failure and no leaked listener', async () => {
  vi.useFakeTimers()
  const child = fakeProcess()
  const stopped = stopOwnedProcess(child as unknown as ChildProcess, 10)
  const rejected = expect(stopped).rejects.toThrow('Owned test backend did not exit')
  await vi.advanceTimersByTimeAsync(20)
  await rejected
  expect(child.kill.mock.calls).toEqual([['SIGTERM'], ['SIGKILL']])
  expect(child.listenerCount('exit')).toBe(0)
  expect(vi.getTimerCount()).toBe(0)
})

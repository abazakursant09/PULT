import type { ChildProcess } from 'child_process'

const exited = (child: ChildProcess) => child.exitCode !== null || child.signalCode !== null

function waitForExit(child: ChildProcess, timeout: number): Promise<void> {
  if (exited(child)) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const done = () => { clearTimeout(timer); resolve() }
    const timer = setTimeout(() => {
      child.removeListener('exit', done)
      reject(new Error('Owned test backend did not exit'))
    }, timeout)
    child.once('exit', done)
  })
}

/** Only a ChildProcess handle created by this test; never discover or kill by port. */
export async function stopOwnedProcess(child: ChildProcess, timeout = 10_000) {
  if (exited(child)) return
  try {
    child.kill('SIGTERM')
    await waitForExit(child, timeout)
  } catch {
    child.kill('SIGKILL')
    await waitForExit(child, timeout)
  }
}

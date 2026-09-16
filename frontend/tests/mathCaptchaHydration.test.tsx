import { act, fireEvent, render, screen, cleanup } from '@testing-library/react'
import { renderToString } from 'react-dom/server'
import { hydrateRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MathCaptcha } from '@/components/MathCaptcha'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('MathCaptcha hydration', () => {
  it('does not generate a challenge during server render and starts disabled', () => {
    const random = vi.spyOn(Math, 'random')
    const html = renderToString(<MathCaptcha onValid={() => {}} />)
    expect(random).not.toHaveBeenCalled()
    expect(html).toContain('Загрузка…')
    expect(html.match(/disabled=""/g)).toHaveLength(2)
  })

  it('hydrates without mismatch when server/client random sources differ', async () => {
    const random = vi.spyOn(Math, 'random').mockReturnValue(0)
    const onValid = vi.fn()
    const container = document.createElement('div')
    container.innerHTML = renderToString(<MathCaptcha onValid={onValid} />)
    document.body.appendChild(container)
    random.mockReturnValue(0.9)
    const recover = vi.fn()
    let root: ReturnType<typeof hydrateRoot>
    try {
      await act(async () => { root = hydrateRoot(container, <MathCaptcha onValid={onValid} />, { onRecoverableError: recover }) })
      expect(recover).not.toHaveBeenCalled()
      expect(container.textContent).toContain('11 + 11')
      expect(container.querySelector('input')).not.toBeDisabled()
      expect(onValid).toHaveBeenLastCalledWith(false)
    } finally {
      await act(async () => { root?.unmount() })
      container.remove()
    }
  })

  it('keeps incorrect/empty answers invalid and refresh revokes a correct answer', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.9)
    const onValid = vi.fn()
    render(<MathCaptcha onValid={onValid} />)
    const input = screen.getByRole('spinbutton')
    expect(onValid).toHaveBeenLastCalledWith(false)
    fireEvent.change(input, { target: { value: '21' } })
    expect(onValid).toHaveBeenLastCalledWith(false)
    fireEvent.change(input, { target: { value: '22' } })
    expect(onValid).toHaveBeenLastCalledWith(true)
    fireEvent.click(screen.getByTitle('Новый пример'))
    expect(input).toHaveValue(null)
    expect(onValid).toHaveBeenLastCalledWith(false)
  })
})

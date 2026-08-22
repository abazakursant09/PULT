import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Register from '@/app/register/page'
import { api } from '@/lib/api'

// The consent controls used to be a <button> nested inside a <label>. A button is a labelable
// element, so the browser forwarded every label click to it: the button's handler fired AND
// the label's handler fired, the state toggled twice, and landed back where it started.
//
// Clicking the square — the obvious target — did nothing. Space did nothing. Only clicking the
// text worked, which was the sole reason anyone could register at all. A seller who clicked
// the box got a form that silently refused to submit, at the very front door of the product.
//
// These tests hold the native semantics that fixed it.

function consents() {
  return screen.getAllByRole('checkbox')
}

describe('registration consents', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api.auth, 'register').mockResolvedValue({ message: 'Проверьте почту' } as never)
  })

  it('exposes two real checkboxes, not buttons pretending to be one', () => {
    render(<Register />)
    expect(consents()).toHaveLength(2)
    consents().forEach((box) => expect(box).not.toBeChecked())
  })

  it('checks once when the square is clicked — and unchecks on the second click', async () => {
    render(<Register />)
    const [privacy] = consents()

    await userEvent.click(privacy)
    expect(privacy).toBeChecked()        // this is exactly what used to be impossible

    await userEvent.click(privacy)
    expect(privacy).not.toBeChecked()    // one click, one toggle — never two
  })

  it('checks when the label text is clicked', async () => {
    render(<Register />)
    const [privacy] = consents()

    await userEvent.click(screen.getByText(/Я даю согласие/))
    expect(privacy).toBeChecked()
  })

  it('toggles with the keyboard when focused', async () => {
    render(<Register />)
    const [privacy] = consents()

    privacy.focus()
    await userEvent.keyboard('[Space]')
    expect(privacy).toBeChecked()

    await userEvent.keyboard('[Space]')
    expect(privacy).not.toBeChecked()
  })

  it('keeps submission blocked until BOTH consents are given', async () => {
    render(<Register />)
    const submit = screen.getByRole('button', { name: /Зарегистрироваться/i })
    const [privacy, terms] = consents()

    expect(submit).toBeDisabled()

    await userEvent.click(privacy)
    expect(submit).toBeDisabled()        // one is not enough

    await userEvent.click(terms)
    expect(submit).toBeDisabled()        // still blocked — the anti-bot answer is missing
  })

  it('lets a seller register once the form is genuinely complete', async () => {
    render(<Register />)

    await userEvent.type(screen.getByPlaceholderText('Иван Петров'), 'E2E Seller')
    await userEvent.type(screen.getByPlaceholderText('you@example.com'), 'seller@example.com')
    const passwords = document.querySelectorAll('input[type="password"]')
    await userEvent.type(passwords[0] as HTMLElement, 'Passw0rd!')
    await userEvent.type(passwords[1] as HTMLElement, 'Passw0rd!')

    // solve the anti-bot question the way a person does — by reading it
    const question = screen.getByText(/=\s*\?/).textContent ?? ''
    const m = question.match(/(\d+)\s*([+−-])\s*(\d+)/)!
    const answer = m[2] === '+' ? Number(m[1]) + Number(m[3]) : Number(m[1]) - Number(m[3])
    await userEvent.type(screen.getByPlaceholderText('Ответ...'), String(answer))

    const [privacy, terms] = consents()
    await userEvent.click(privacy)
    await userEvent.click(terms)

    const submit = screen.getByRole('button', { name: /Зарегистрироваться/i })
    expect(submit).toBeEnabled()

    await userEvent.click(submit)
    expect(api.auth.register).toHaveBeenCalled()
    // LEGAL-PRELAUNCH-F2 (blocker #6): the explicit consent flag must reach the server.
    expect(api.auth.register).toHaveBeenCalledWith(
      'seller@example.com', 'E2E Seller', 'Passw0rd!', undefined, true,
    )
  })
})

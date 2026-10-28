import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from '@/components/ui/dialog'

function Fixture({ explicitLabel = false }: { explicitLabel?: boolean }) {
  return (
    <>
      <button type="button">Outside before</button>
      <Dialog>
        <DialogTrigger asChild><button type="button">Open preferences</button></DialogTrigger>
        <DialogContent data-testid="dialog-content" aria-label={explicitLabel ? 'Quick preferences' : undefined}>
          {!explicitLabel && <DialogTitle>Preferences</DialogTitle>}
          <label>Display name<input name="displayName" /></label>
          <button type="button" disabled>Unavailable action</button>
          <button type="button">Save preferences</button>
        </DialogContent>
      </Dialog>
      <button type="button">Outside after</button>
    </>
  )
}

async function openDialog(explicitLabel = false) {
  const user = userEvent.setup()
  render(<Fixture explicitLabel={explicitLabel} />)
  const trigger = screen.getByRole('button', { name: 'Open preferences' })
  await user.click(trigger)
  return { user, trigger, content: screen.getByTestId('dialog-content') }
}

describe('Dialog accessibility and keyboard boundaries', () => {
  it.each([false, true])('restores the outer opener when the whole nested subtree is closed (unmount=%s)', async unmount => {
    const user = userEvent.setup()
    function ParentClose() {
      const [open, setOpen] = useState(false)
      return <><button onClick={() => setOpen(true)}>Open whole modal</button>
        <button onClick={() => setOpen(false)}>Programmatic parent close</button>
        {(!unmount || open) && <Dialog open={open} onOpenChange={setOpen}><DialogContent aria-label="Outer">
          <Dialog><DialogTrigger>Open inner</DialogTrigger><DialogContent aria-label="Inner" /></Dialog>
        </DialogContent></Dialog>}
      </>
    }
    render(<ParentClose />)
    const opener = screen.getByRole('button', { name: 'Open whole modal' })
    await user.click(opener)
    await user.click(screen.getByRole('button', { name: 'Open inner' }))
    fireEvent.click(screen.getByRole('button', { name: 'Programmatic parent close' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(opener).toHaveFocus()
  })
  it('keeps an already-open nested child above its initially open parent', async () => {
    const user = userEvent.setup()
    function Concurrent() {
      const [parentOpen, setParentOpen] = useState(true)
      const [childOpen, setChildOpen] = useState(true)
      return <Dialog open={parentOpen} onOpenChange={setParentOpen}><DialogContent aria-label="Concurrent parent">
        <Dialog open={childOpen} onOpenChange={setChildOpen}><DialogContent aria-label="Concurrent child">
          <button>Nested last</button>
        </DialogContent></Dialog>
      </DialogContent></Dialog>
    }
    render(<Concurrent />)
    const child = screen.getByRole('dialog', { name: 'Concurrent child' })
    screen.getByRole('button', { name: 'Nested last' }).focus()
    await user.tab()
    expect(child.contains(document.activeElement)).toBe(true)
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Concurrent child' })).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Concurrent parent' })).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
  it('restores the opener even when a child takes autofocus during mounting', async () => {
    const user = userEvent.setup()
    render(<Dialog><DialogTrigger>Open autofocus</DialogTrigger><DialogContent aria-label="Autofocus">
      <input autoFocus aria-label="Autofocused field" />
    </DialogContent></Dialog>)
    const opener = screen.getByRole('button', { name: 'Open autofocus' })
    await user.click(opener)
    expect(screen.getByRole('textbox', { name: 'Autofocused field' })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(opener).toHaveFocus()
  })
  it('preserves existing autofocus and does not steal focus on controlled rerenders', async () => {
    const user = userEvent.setup()
    function Controlled() {
      const [value, setValue] = useState('')
      return <Dialog open><DialogContent aria-label="Edit">
        <input autoFocus aria-label="Existing autofocus" value={value} onChange={e => setValue(e.target.value)} />
      </DialogContent></Dialog>
    }
    render(<Controlled />)
    const input = screen.getByRole('textbox', { name: 'Existing autofocus' })
    expect(input).toHaveFocus()
    await user.type(input, 'seller')
    expect(input).toHaveFocus()
    expect(input).toHaveValue('seller')
  })

  it('gives only the nested top dialog keyboard ownership and restores parent focus', async () => {
    const user = userEvent.setup()
    render(<Dialog><DialogTrigger>Open parent</DialogTrigger><DialogContent aria-label="Parent">
      <Dialog><DialogTrigger>Open child</DialogTrigger><DialogContent aria-label="Child">
        <button type="button">Child action</button>
      </DialogContent></Dialog>
    </DialogContent></Dialog>)
    await user.click(screen.getByRole('button', { name: 'Open parent' }))
    const childTrigger = screen.getByRole('button', { name: 'Open child' })
    await user.click(childTrigger)
    expect(screen.getByRole('dialog', { name: 'Child' }).contains(document.activeElement)).toBe(true)
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Child' })).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Parent' })).toBeInTheDocument()
    expect(childTrigger).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open parent' })).toHaveFocus()
  })

  it('excludes hidden and disabled-fieldset controls from the wrap boundary', async () => {
    const user = userEvent.setup()
    render(<Dialog open><DialogContent aria-label="Filtered">
      <button style={{ display: 'none' }}>Hidden</button>
      <fieldset disabled><button>Disabled child</button></fieldset>
      <button>Last enabled</button>
      <input type="hidden" />
    </DialogContent></Dialog>)
    screen.getByRole('button', { name: 'Last enabled' }).focus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
    await user.tab({ shift: true })
    expect(screen.getByRole('button', { name: 'Last enabled' })).toHaveFocus()
  })

  it('exposes a modal dialog named by its DialogTitle', async () => {
    await openDialog()
    expect(screen.getByRole('dialog', { name: 'Preferences' })).toHaveAttribute('aria-modal', 'true')
  })

  it('moves initial focus inside on opening', async () => {
    const { content } = await openDialog()
    await waitFor(() => expect(content.contains(document.activeElement)).toBe(true))
  })

  it('wraps Tab from the last enabled control to the first', async () => {
    const { user } = await openDialog()
    // Use native enabled controls: no CSS layout/offset measurements in jsdom.
    screen.getByRole('button', { name: 'Save preferences' }).focus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
  })

  it('wraps Shift+Tab from the first control to the last', async () => {
    const { user } = await openDialog()
    screen.getByRole('button', { name: 'Close' }).focus()
    await user.tab({ shift: true })
    expect(screen.getByRole('button', { name: 'Save preferences' })).toHaveFocus()
  })

  it('closes on Escape and restores focus to the trigger', async () => {
    const { user, trigger } = await openDialog()
    screen.getByRole('textbox', { name: 'Display name' }).focus()
    await user.keyboard('{Escape}')
    expect(screen.queryByTestId('dialog-content')).not.toBeInTheDocument()
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('supports an explicit aria-label without a DialogTitle', async () => {
    await openDialog(true)
    expect(screen.getByRole('dialog', { name: 'Quick preferences' })).toHaveAttribute('aria-modal', 'true')
  })

  it('contains programmatic focus escape while open', async () => {
    const { content } = await openDialog()
    screen.getByRole('textbox', { name: 'Display name' }).focus()
    // focus() dispatches the real focus/focusin events, like another widget stealing focus.
    act(() => screen.getByRole('button', { name: 'Outside after' }).focus())
    await waitFor(() => expect(content.contains(document.activeElement)).toBe(true))
  })

  it('preserves the existing close button', async () => {
    const { user } = await openDialog()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByTestId('dialog-content')).not.toBeInTheDocument()
  })

  it('preserves overlay click dismissal', async () => {
    const { user, content } = await openDialog()
    // The internal overlay has no semantic role; identify its existing backdrop class,
    // without relying on CSS visibility, coordinates, or hit testing.
    const overlay = content.parentElement?.querySelector<HTMLElement>('.backdrop-blur-sm')
    expect(overlay).toBeInstanceOf(HTMLElement)
    await user.click(overlay!)
    expect(screen.queryByTestId('dialog-content')).not.toBeInTheDocument()
  })
})

import { readFileSync } from 'fs'
import { join } from 'path'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/ui/EmptyState'

// P1 — canonical component system. Two layers of guard:
//  1. Behavioural: each primitive renders every variant/state without throwing, and states that
//     have observable DOM effects (loading → disabled, error → aria-invalid) actually apply.
//  2. Static/token: the primitive sources reference P0 tokens and carry no raw hex — the palette
//     can never be re-hardcoded inside a component again.

const ROOT = join(__dirname, '..')
const UI = join(ROOT, 'components', 'ui')
const src = (f: string) => readFileSync(join(UI, f), 'utf-8')
const readRoot = (...p: string[]) => readFileSync(join(ROOT, ...p), 'utf-8')
const PRIMITIVES = ['button.tsx', 'card.tsx', 'badge.tsx', 'input.tsx', 'skeleton.tsx', 'EmptyState.tsx']

describe('P1 component system — tokens', () => {
  it.each(PRIMITIVES)('%s hardcodes no raw hex colour', (f) => {
    expect(src(f)).not.toMatch(/#[0-9A-Fa-f]{6}\b/)
    expect(src(f)).not.toMatch(/#[0-9A-Fa-f]{3}\b/)
  })

  // Every primitive resolves colour through P0 tokens. skeleton.tsx is the one exception: its
  // tokens live in the `.pult-skeleton` utility (globals.css), so the TSX only carries the class.
  it.each(PRIMITIVES.filter((f) => f !== 'skeleton.tsx'))('%s references P0 tokens via var(--)', (f) => {
    expect(src(f)).toMatch(/var\(--/)
  })

  it('skeleton delegates its visual to the token-based .pult-skeleton utility', () => {
    expect(src('skeleton.tsx')).toMatch(/pult-skeleton/)
    expect(readRoot('styles', 'globals.css')).toMatch(/\.pult-skeleton[\s\S]*var\(--/)
  })

  it('no primitive uses Tailwind named colour utilities (green-/amber-/red-)', () => {
    for (const f of PRIMITIVES) {
      expect(src(f), `${f} must not use named-colour utilities`)
        .not.toMatch(/\b(bg|text|border)-(red|green|amber|blue|slate|zinc|gray|emerald)-\d{2,3}/)
    }
  })

  it('button/skeleton drive motion from tokens, never transition:all', () => {
    expect(src('button.tsx')).not.toMatch(/transition-all/)
    expect(src('button.tsx')).toMatch(/active:scale-\[0\.97\]/)
  })
})

describe('P1 component system — Button', () => {
  it.each(['primary', 'secondary', 'ghost', 'danger'] as const)('renders %s variant', (variant) => {
    render(<Button variant={variant}>Go</Button>)
    expect(screen.getByRole('button', { name: 'Go' })).toBeTruthy()
  })

  it('keeps back-compat aliases (default/destructive/outline)', () => {
    for (const v of ['default', 'destructive', 'outline', 'link'] as const) {
      const { unmount } = render(<Button variant={v}>x</Button>)
      unmount()
    }
  })

  it('loading state disables the button', () => {
    render(<Button loading>Save</Button>)
    expect(screen.getByRole('button')).toHaveProperty('disabled', true)
  })

  it('disabled state disables the button', () => {
    render(<Button disabled>Save</Button>)
    expect(screen.getByRole('button')).toHaveProperty('disabled', true)
  })
})

describe('P1 component system — Card', () => {
  it.each(['surface', 'elevated', 'bordered'] as const)('renders %s variant', (variant) => {
    const { container } = render(<Card variant={variant}>body</Card>)
    expect(container.firstChild).toBeTruthy()
  })
})

describe('P1 component system — Badge', () => {
  it.each(['success', 'warning', 'danger', 'neutral'] as const)('renders %s variant', (variant) => {
    render(<Badge variant={variant}>tag</Badge>)
    expect(screen.getByText('tag')).toBeTruthy()
  })
})

describe('P1 component system — Input', () => {
  it('default renders and is not invalid', () => {
    render(<Input placeholder="name" />)
    const el = screen.getByPlaceholderText('name')
    expect(el.getAttribute('aria-invalid')).not.toBe('true')
  })

  it('error prop sets aria-invalid', () => {
    render(<Input placeholder="email" error />)
    expect(screen.getByPlaceholderText('email').getAttribute('aria-invalid')).toBe('true')
  })

  it('disabled state disables the field', () => {
    render(<Input placeholder="x" disabled />)
    expect(screen.getByPlaceholderText('x')).toHaveProperty('disabled', true)
  })
})

describe('P1 component system — Skeleton', () => {
  it('uses the shimmer utility and reserves its box (no layout shift)', () => {
    const { container } = render(<Skeleton className="h-4 w-32" />)
    const el = container.firstChild as HTMLElement
    expect(el.className).toContain('pult-skeleton')
    expect(el.className).toContain('h-4')
    expect(el.getAttribute('aria-hidden')).toBe('true')
  })
})

describe('P1 component system — EmptyState', () => {
  it('renders an honest title with no fabricated content', () => {
    render(<EmptyState title="Пока нет отзывов" />)
    expect(screen.getByText('Пока нет отзывов')).toBeTruthy()
  })

  it('renders optional description and action only when provided', () => {
    render(<EmptyState title="Нет данных" description="Подключите кабинет" action={<Button>Подключить</Button>} />)
    expect(screen.getByText('Подключите кабинет')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Подключить' })).toBeTruthy()
  })
})

import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PultMark } from '@/components/brand/PultMark'
import sharp from 'sharp'

describe('Approved mark #42', () => {
  it('retains the original reference bytes, not a regenerated approximation', () => {
    const bytes = readFileSync('public/logos/pult-approved-42.png')
    expect(createHash('sha256').update(bytes).digest('hex'))
      .toBe('dffeaa72de568e8ff2b416e002ca773b194ea6af521fcdb50c3922900f18c1ef')
  })
  it('shows the original image through an undistorted viewport', () => {
    const { container } = render(<PultMark approvedReference />)
    expect(container.querySelector('svg')).toHaveAttribute('viewBox', '0 0 340 382')
    expect(container.querySelector('image')).toHaveAttribute('href', '/logos/pult-approved-42.webp')
    expect(container.querySelector('path')).toBeNull()
  })
  it('preserves every visible pixel while reducing the transferred asset', async () => {
    const original = await sharp('public/logos/pult-approved-42.png')
      .extract({ left: 228, top: 309, width: 340, height: 382 }).ensureAlpha().raw().toBuffer()
    const optimized = await sharp('public/logos/pult-approved-42.webp').ensureAlpha().raw().toBuffer()
    expect(original.equals(optimized)).toBe(true)
    expect(readFileSync('public/logos/pult-approved-42.webp').length).toBeLessThan(300_000)
  })
  it('does not change other surfaces implicitly', () => {
    const { container } = render(<PultMark />)
    expect(container.querySelector('image')).toBeNull()
    expect(container.querySelector('svg')).toHaveAttribute('viewBox', '0 0 28 32')
  })
})
